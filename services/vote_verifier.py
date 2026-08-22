import asyncio
import logging
import html
from aiogram import Bot
from sqlalchemy import select, update
from database.models import VotesHistory, VoteStatus, User, ProjectSettings
from database.engine import async_session
from services.openbudget import OpenBudgetService
import database.crud as crud

logger = logging.getLogger(__name__)

# Har bir loyiha bo'yicha saytdagi oxirgi ma'lum ovozlar soni
_baseline_counts: dict[str, int] = {}


async def verify_pending_votes_step(bot: Bot):
    """
    Kutilayotgan ovozlarni (PENDING_VERIFY) sayt orqali tekshiradi va 
    faqat saytda ovoz soni oshgandagina foydalanuvchi balansini to'ldiradi.
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

    for project_id, v_list in projects_map.items():
        try:
            initiative = await OpenBudgetService.find_initiative(project_id)
            if not initiative:
                continue

            current_count = int(initiative.get("voteCount") or 0)
            
            # Agar birinchi marta tekshirilayotgan bo'lsa, joriy sonni baseline qilamiz
            if project_id not in _baseline_counts:
                _baseline_counts[project_id] = current_count
                logger.info(f"Loyiha {project_id} uchun boshlang'ich ovozlar soni o'rnatildi: {current_count}")
                continue

            baseline = _baseline_counts[project_id]

            if current_count > baseline:
                delta = current_count - baseline
                to_confirm = v_list[:delta]
                logger.info(f"Loyiha {project_id}: saytda ovozlar soni {baseline} -> {current_count} ga oshdi (+{delta}). {len(to_confirm)} ta ovoz tasdiqlanmoqda.")

                for vote in to_confirm:
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
                                        f"Siz taklif qilgan foydalanuvchi ({html.escape(str(user.username or vote.telegram_id))}) ovozi Open Budget saytida rasman hisoblandi!\n"
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
                                    f"✅ <b>TABRIKLAYMIZ! Ovozingiz rasman hisoblandi!</b>\n\n"
                                    f"🏛 <i>Open Budget saytida ovozingiz muvaffaqiyatli qabul qilindi.</i>\n"
                                    f"💰 Balansingizga: <b>+{voter_reward:,.0f} so'm</b> qo'shildi!\n\n"
                                    f"Do'stlaringizni taklif qiling va ko'proq daromad oling! 👥"
                                ),
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.warning(f"Ovoz beruvchiga xabar yuborishda xato: {e}")

                _baseline_counts[project_id] = current_count
            elif current_count < baseline:
                # Saytda qayta hisoblash yoki filtratsiya bo'lsa yangilaymiz
                _baseline_counts[project_id] = current_count

        except Exception as e:
            logger.error(f"Loyiha {project_id} ovozlarini tekshirishda xato: {e}")


async def start_vote_verifier_background_task(bot: Bot):
    """Orqa fonda har 15 soniyada saytni tekshirib boruvchi doimiy xizmat"""
    logger.info("Open Budget saytdan ovozlarni tasdiqlash fon xizmati boshlandi...")
    while True:
        try:
            await verify_pending_votes_step(bot)
        except asyncio.CancelledError:
            logger.info("Vote verifier fon xizmati to'xtatildi.")
            break
        except Exception as e:
            logger.error(f"Vote verifier asosiy tsiklida xatolik: {e}")
        
        await asyncio.sleep(15)
