import asyncio
import logging
import html
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from sqlalchemy import select, update
from database.models import VotesHistory, VoteStatus, User, ProjectSettings
from database.session import async_session
from services.openbudget import OpenBudgetService
import database.crud as crud

logger = logging.getLogger(__name__)

# Har bir loyiha bo'yicha saytdagi oxirgi ma'lum ovozlar soni
_baseline_counts: dict[str, int] = {}


def match_phone_mask(real_phone: str, portal_masked: str) -> bool:
    """
    real_phone: '998901234567' yoki '+998 90 123-45-67'
    portal_masked: '**-*96-99-20' yoki '**-*36-07-50' yoki '+99899***6030'
    """
    if not real_phone or not portal_masked:
        return False
    clean_real = "".join(filter(str.isdigit, str(real_phone)))
    if clean_real.startswith("998") and len(clean_real) == 12:
        clean_real = clean_real[3:]
        
    clean_masked = "".join(filter(str.isdigit, str(portal_masked)))
    
    # OpenBudget odatda **-*XX-XX-XX formatida qaytaradi, ya'ni oxirgi 6 raqam ko'rinadi
    if len(clean_masked) >= 4 and clean_real.endswith(clean_masked):
        return True
        
    return False


async def confirm_single_vote(db, bot: Bot, vote: VotesHistory):
    """Bitta ovozni tasdiqlaydi, hisobiga pul o'tkazadi va Telegramda tabriklaydi"""
    clean_phone = "".join(filter(str.isdigit, vote.phone_number))
    
    # 1. Ovoz holatini SUCCESS ga o'tkazamiz
    await db.execute(
        update(VotesHistory)
        .where(VotesHistory.id == vote.id)
        .values(status=VoteStatus.SUCCESS)
    )

    settings = await crud.get_project_settings(db)
    voter_reward = settings.voter_reward
    referral_price = settings.referral_price

    # 2. Ovoz bergan foydalanuvchi hisobiga pul o'tkazamiz
    if voter_reward > 0:
        await db.execute(
            update(User)
            .where(User.telegram_id == vote.telegram_id)
            .values(balance=User.balance + voter_reward)
        )

    # 3. Agar referal bo'lsa, taklif qilgan odamga ham referal mukofotini beramiz
    user_rec = await db.get(User, vote.telegram_id)
    if user_rec and user_rec.username:
        # Taklif qilgan foydalanuvchini (referrer) aniqlaymiz
        referrer = await crud.get_referrer_by_ref_username(db, user_rec.username)
        if referrer and referral_price > 0:
            await db.execute(
                update(User)
                .where(User.telegram_id == referrer.telegram_id)
                .values(balance=User.balance + referral_price)
            )
            # Referrerga xabar beramiz
            try:
                await bot.send_message(
                    chat_id=referrer.telegram_id,
                    text=(
                        f"💰 <b>Hamkorlik mukofoti!</b>\n\n"
                        f"Siz taklif qilgan do'stingiz muvaffaqiyatli ovoz berdi. "
                        f"Balansingizga <b>+{referral_price:,.0f} so'm</b> qo'shildi! 🚀"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Referrerga xabar yuborishda xato: {e}")

    await db.commit()

    # 4. Foydalanuvchiga Telegramda xabar beramiz
    try:
        clean_d = clean_phone[-9:] if len(clean_phone) >= 9 else clean_phone
        formatted_p = f"+998 ({clean_d[:2]}) {clean_d[2:5]}-{clean_d[5:7]}-{clean_d[7:]}"
        await bot.send_message(
            chat_id=vote.telegram_id,
            text=(
                f"✨ <b>TABRIKLAYMIZ! Ovozingiz rasman tasdiqlandi!</b> 🔥\n\n"
                f"🏛 <code>{formatted_p}</code> raqami uchun ovoz Open Budget portalida tasdiqlandi.\n"
                f"💰 Mukofotingiz balansingizga qo'shildi. Davom eting! 🚀"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Foydalanuvchiga muvaffaqiyat xabari yuborishda xato: {e}")


async def verify_pending_votes_step(bot: Bot):
    """
    Kutilayotgan ovozlarni (PENDING_VERIFY):
    1. Open Budget rasmiy 'Ovozlar ro'yxati' (votes list) jadvalidan qidirib tasdiqlaydi.
    2. Saytdagi umumiy hisoblagich (voteCount) orqali zaxira tekshiruvini olib boradi.
    """
    async with async_session() as db:
        stmt = (
            select(VotesHistory)
            .where(VotesHistory.status == VoteStatus.PENDING_VERIFY)
            .order_by(VotesHistory.id.asc())
        )
        result = await db.execute(stmt)
        pending_votes = result.scalars().all()

    if not pending_votes:
        return

    # Loyihalar bo'yicha guruhlaymiz
    projects_map: dict[str, list[VotesHistory]] = {}
    for v in pending_votes:
        projects_map.setdefault(v.project_id, []).append(v)

    now = datetime.now(timezone.utc)

    for project_id, v_list in projects_map.items():
        try:
            confirmed_set = set()

            # 1. 'Ovozlar ro'yxati' jadvalidan raqamlarni olib tekshiramiz
            official_votes = await OpenBudgetService.get_official_votes_list(project_id, page=0, size=50)
            if official_votes:
                # Kopiya qilamiz, undan ishlatilganlarni o'chirib boramiz (double spend/match ning oldini olish uchun)
                available_portal_votes = list(official_votes)
                logger.info(f"Loyiha {project_id}: 'Ovozlar ro'yxati'da {len(available_portal_votes)} ta raqam tekshirilmoqda...")
                
                for vote in v_list:
                    clean_phone = "".join(filter(str.isdigit, vote.phone_number))
                    if clean_phone.startswith("998") and len(clean_phone) == 12:
                        clean_phone = clean_phone[3:]
                        
                    matched_row = None
                    for row in available_portal_votes:
                        portal_phone = row.get("phoneNumber") or row.get("phone_number") or row.get("phone") or ""
                        if match_phone_mask(clean_phone, portal_phone):
                            matched_row = row
                            break
                            
                    if matched_row:
                        logger.info(f"🎯 100% MOS KELDI: {clean_phone} -> {matched_row.get('phoneNumber', '')} (Open Budget Ovozlar ro'yxatida aniqlandi!)")
                        confirmed_set.add(vote.id)
                        available_portal_votes.remove(matched_row) # Ushbu portal qatorini band qilamiz
                        async with async_session() as db:
                            await confirm_single_vote(db, bot, vote)

            remaining_votes = [v for v in v_list if v.id not in confirmed_set]

            # 2. 2 soatdan (120 daqiqadan) oshgan va tasdiqlanmagan kutilayotgan ovozlarni rad etamiz (bekor qilamiz)
            for vote in remaining_votes:
                if vote.id not in confirmed_set and vote.created_at:
                    v_time = vote.created_at
                    if v_time.tzinfo is None:
                        v_time = v_time.replace(tzinfo=timezone.utc)
                    if (now - v_time).total_seconds() > 7200: # 2 soat
                        logger.warning(f"❌ Ovoz topilmadi (2 soat o'tdi): {vote.phone_number}. Rad etilmoqda.")
                        async with async_session() as db:
                            # Bazadagi statusini FAILED ga o'zgartiramiz
                            db_vote = await db.get(VotesHistory, vote.id)
                            if db_vote:
                                db_vote.status = VoteStatus.FAILED
                                await db.commit()
                                
                                # Foydalanuvchiga xabar beramiz
                                try:
                                    await bot.send_message(
                                        chat_id=vote.telegram_id,
                                        text=(
                                            f"❌ <b>Ovoz tasdiqlanmadi!</b>\n\n"
                                            f"Siz kiritgan <code>+{vote.phone_number}</code> raqami Open Budget portalidan "
                                            f"tasdiqlanmadi (Ovozlar ro'yxatida topilmadi). Pul balansingizga qo'shilmadi."
                                        ),
                                        parse_mode="HTML"
                                    )
                                except Exception as send_err:
                                    logger.warning(f"Xabar yuborishda xato: {send_err}")

        except Exception as e:
            logger.error(f"Loyiha {project_id} ovozlarini tekshirishda xato: {e}")


async def start_vote_verifier_background_task(bot: Bot):
    """Orqa fonda rasmiy 'Ovozlar ro'yxati'dan qidirib boruvchi doimiy xizmat"""
    logger.info("Open Budget rasmiy 'Ovozlar ro'yxati' orqali tekshirish xizmati ishga tushdi...")
    await asyncio.sleep(5)
    
    while True:
        try:
            await verify_pending_votes_step(bot)
        except asyncio.CancelledError:
            logger.info("Vote verifier fon xizmati to'xtatildi.")
            break
        except Exception as e:
            logger.error(f"Vote verifier asosiy tsiklida xatolik: {e}")
        
        # Har 5 daqiqada (300 soniyada) tekshirib turadi
        await asyncio.sleep(300)
