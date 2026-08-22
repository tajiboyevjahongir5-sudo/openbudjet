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


async def verify_pending_votes_step(bot: Bot):
    """
    Kutilayotgan ovozlarni (PENDING_VERIFY) saytning rasmiy statistikasi (voteCount)
    orqali TEKSHIRADI (Foydalanuvchiga hech qanday ortiqcha SMS bormaydi!).
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
            # Saytdagi loyiha ma'lumotlarini olamiz (hech qanday SMS yoki captcha ketmaydi)
            initiative = await OpenBudgetService.find_initiative(project_id)
            current_count = int(initiative.get("voteCount") or 0) if initiative else 0
            
            # Agar birinchi marta tekshirilayotgan bo'lsa, joriy sonni baseline qilamiz
            if project_id not in _baseline_counts:
                _baseline_counts[project_id] = current_count
                logger.info(f"Loyiha {project_id} uchun boshlang'ich ovozlar soni: {current_count}")
                baseline = current_count
            else:
                baseline = _baseline_counts[project_id]

            # Saytda ovoz soni oshgan bo'lsa yoki ovoz berilganiga 15 daqiqadan oshgan bo'lsa
            to_confirm = []
            if current_count > baseline:
                delta = current_count - baseline
                to_confirm.extend(v_list[:delta])
                _baseline_counts[project_id] = current_count
                logger.info(f"Loyiha {project_id}: saytda ovozlar soni {baseline} -> {current_count} ga oshdi (+{delta}).")

            # Shuningdek, berilganiga 20 daqiqadan oshgan kutilayotgan ovozlarni ham tasdiqlaymiz (chunki portal ovozni qabul qilgan)
            for v in v_list:
                if v not in to_confirm and v.created_at:
                    v_time = v.created_at
                    if v_time.tzinfo is None:
                        v_time = v_time.replace(tzinfo=timezone.utc)
                    if (now - v_time).total_seconds() > 1200:  # 20 daqiqa
                        to_confirm.append(v)

            if not to_confirm:
                continue

            logger.info(f"{len(to_confirm)} ta ovoz rasman tasdiqlanmoqda va hisoblar to'ldirilmoqda.")

            for vote in to_confirm:
                clean_phone = "".join(filter(str.isdigit, vote.phone_number))
                async with async_session() as db:
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
                    try:
                        await bot.send_message(
                            chat_id=vote.telegram_id,
                            text=(
                                f"✅ <b>TABRIKLAYMIZ! Ovozingiz rasman tasdiqlandi!</b>\n\n"
                                f"🏛 <code>+{clean_phone}</code> raqamingiz orqali berilgan ovoz Open Budget portalida muvaffaqiyatli hisoblandi.\n"
                                f"💰 Balansingizga: <b>+{voter_reward:,.0f} so'm</b> qo'shildi!\n\n"
                                f"Do'stlaringizni taklif qiling va ko'proq daromad oling! 👥"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Ovoz beruvchiga xabar yuborishda xato: {e}")

        except Exception as e:
            logger.error(f"Loyiha {project_id} ovozlarini tekshirishda xato: {e}")


async def start_vote_verifier_background_task(bot: Bot):
    """
    Orqa fonda ovozlarni mutlaqo jim (SMS yubormasdan), 
    faqat saytdagi rasmiy hisoblagich orqali tekshiruvchi doimiy xizmat.
    """
    logger.info("Ovozlarni jim tekshirish xizmati ishga tushdi (SMS yuborilmaydi)...")
    await asyncio.sleep(15)
    
    while True:
        try:
            await verify_pending_votes_step(bot)
        except asyncio.CancelledError:
            logger.info("Vote verifier fon xizmati to'xtatildi.")
            break
        except Exception as e:
            logger.error(f"Vote verifier asosiy tsiklida xatolik: {e}")
        
        # Har 1 daqiqada sayt hisoblagichini tekshirib turadi
        await asyncio.sleep(60)
