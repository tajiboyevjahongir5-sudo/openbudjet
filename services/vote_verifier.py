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
    clean_masked = "".join(filter(str.isdigit, str(portal_masked)))
    
    if len(clean_masked) >= 4 and clean_real.endswith(clean_masked):
        return True
        
    if "*" in str(portal_masked):
        parts = [p for p in str(portal_masked).split("*") if p]
        if parts:
            last_part = "".join(filter(str.isdigit, parts[-1]))
            if len(last_part) >= 4 and clean_real.endswith(last_part):
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

    # 3. Agar referal orqali kelgan bo'lsa, taklif qilganga ham pul o'tkazamiz
    user = await crud.get_user(db, vote.telegram_id)
    if user and user.invited_by and referral_price > 0:
        await db.execute(
            update(User)
            .where(User.telegram_id == user.invited_by)
            .values(
                balance=User.balance + referral_price,
                total_referrals=User.total_referrals + 1
            )
        )
        try:
            await bot.send_message(
                chat_id=user.invited_by,
                text=(
                    f"🎉 <b>Yangi referal mukofoti!</b>\n\n"
                    f"Siz taklif qilgan foydalanuvchi ({html.escape(str(user.username or vote.telegram_id))}) ovozi Open Budget portalida rasman tasdiqlandi!\n"
                    f"💵 Balansingizga <b>+{referral_price:,.0f} so'm</b> qo'shildi!"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Referrerga xabar yuborishda xato: {e}")

    await db.commit()

    # 4. Foydalanuvchiga muvaffaqiyatli tasdiq xabarini yuboramiz
    clean_d = clean_phone[-9:] if len(clean_phone) >= 9 else clean_phone
    formatted_p = f"+998 ({clean_d[:2]}) {clean_d[2:5]}-{clean_d[5:7]}-{clean_d[7:]}"
    try:
        await bot.send_message(
            chat_id=vote.telegram_id,
            text=(
                f"✅ <b>TABRIKLAYMIZ! Ovozingiz rasman tasdiqlandi!</b>\n\n"
                f"🏛 <code>{formatted_p}</code> raqamingiz Open Budget rasmiy <b>«Ovozlar ro'yxati»</b>da muvaffaqiyatli aniqlandi.\n"
                f"💰 Balansingizga: <b>+{voter_reward:,.0f} so'm</b> qo'shildi!\n\n"
                f"💡 <i>Balansingizni «💎 Mening hisobim» bo'limida ko'rishingiz mumkin.</i>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Ovoz beruvchiga xabar yuborishda xato: {e}")


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
                logger.info(f"Loyiha {project_id}: 'Ovozlar ro'yxati'da {len(official_votes)} ta raqam tekshirilmoqda...")
                for vote in v_list:
                    clean_phone = "".join(filter(str.isdigit, vote.phone_number))
                    for row in official_votes:
                        portal_phone = row.get("phoneNumber") or row.get("phone_number") or row.get("phone") or ""
                        if match_phone_mask(clean_phone, portal_phone):
                            logger.info(f"🎯 100% MOS KELDI: {clean_phone} -> {portal_phone} (Open Budget Ovozlar ro'yxatida aniqlandi!)")
                            confirmed_set.add(vote.id)
                            async with async_session() as db:
                                await confirm_single_vote(db, bot, vote)
                            break

            # 2. Zaxira tekshiruvi: Agar saytda umumiy ovozlar soni oshgan bo'lsa
            initiative = await OpenBudgetService.find_initiative(project_id)
            current_count = int(initiative.get("voteCount") or 0) if initiative else 0
            
            if project_id not in _baseline_counts:
                _baseline_counts[project_id] = current_count
                baseline = current_count
            else:
                baseline = _baseline_counts[project_id]

            remaining_votes = [v for v in v_list if v.id not in confirmed_set]
            if current_count > baseline:
                delta = current_count - baseline
                to_confirm_delta = remaining_votes[:delta]
                _baseline_counts[project_id] = current_count
                for vote in to_confirm_delta:
                    confirmed_set.add(vote.id)
                    async with async_session() as db:
                        await confirm_single_vote(db, bot, vote)

            # 3. 20 daqiqadan oshgan kutilayotgan ovozlar
            for vote in remaining_votes:
                if vote.id not in confirmed_set and vote.created_at:
                    v_time = vote.created_at
                    if v_time.tzinfo is None:
                        v_time = v_time.replace(tzinfo=timezone.utc)
                    if (now - v_time).total_seconds() > 1200:
                        async with async_session() as db:
                            await confirm_single_vote(db, bot, vote)

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
        
        # Har 15 soniyada tekshirib turadi
        await asyncio.sleep(15)
