import asyncio
import logging
import html
from datetime import datetime, timedelta
from aiogram import Bot
from sqlalchemy import select, update
from database.models import VotesHistory, VoteStatus, User, ProjectSettings
from database.session import async_session
from services.openbudget import OpenBudgetService
from services.captcha_solver import solve_captcha
import database.crud as crud

logger = logging.getLogger(__name__)


async def verify_single_vote_on_portal(vote: VotesHistory, bot: Bot) -> bool:
    """
    Bitta telefon raqami bo'yicha Open Budget portaliga so'rov yuborib, 
    aynan shu raqamdan ovoz qabul qilinganligini (already_voted) 100% aniq tekshiradi.
    """
    clean_phone = "".join(filter(str.isdigit, vote.phone_number))
    
    # 1. Captcha yuklaymiz
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    if not success_cap or not cap_data:
        logger.warning(f"Vote verifier: {clean_phone} uchun captcha yuklab bo'lmadi: {cap_msg}")
        return False

    captcha_key = cap_data.get("key")
    captcha_image = cap_data.get("image_base64")

    # 2. Captchani yechamiz (2Captcha / Gemini)
    auto_result = await solve_captcha(captcha_image)
    if auto_result is None:
        logger.warning(f"Vote verifier: {clean_phone} uchun captcha yechilmadi")
        return False

    # 3. Portalga tekshiruv so'rovi yuboramiz
    success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
        phone_number=clean_phone,
        project_id=vote.project_id,
        captcha_key=captcha_key,
        captcha_result=auto_result
    )

    # 4. Portal javobini tahlil qilamiz:
    voted_terms = ["already_voted", "allaqachon", "ovoz berilgan", "овоз беrilgan", "овоз берган", "бошқа рақам"]
    is_confirmed_voted = (
        not success and 
        (error_msg == "already_voted" or any(t in str(error_msg).lower() for t in voted_terms))
    )

    if is_confirmed_voted:
        logger.info(f"✅ VOTE VERIFIED 100%: {clean_phone} raqamining ovozi Open Budget portalida rasman tasdiqlandi!")
        
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

        return True

    logger.info(f"Vote verifier: {clean_phone} hali navbatda (portal javobi: {error_msg})")
    return False


async def verify_pending_votes_step(bot: Bot):
    """
    Kutilayotgan barcha ovozlarni (PENDING_VERIFY) navbat bilan alohida tekshiradi.
    """
    async with async_session() as db:
        stmt = (
            select(VotesHistory)
            .where(VotesHistory.status == VoteStatus.PENDING_VERIFY)
            .order_by(VotesHistory.id.asc())
            .limit(5)
        )
        result = await db.execute(stmt)
        pending_votes = result.scalars().all()

    if not pending_votes:
        return

    logger.info(f"Vote verifier: {len(pending_votes)} ta kutilayotgan ovoz tekshirilmoqda...")

    for vote in pending_votes:
        try:
            await verify_single_vote_on_portal(vote, bot)
            # Har bir so'rov oralig'ida 5 soniya tanaffus qilamiz
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Ovozni tekshirishda xatolik ({vote.phone_number}): {e}")


async def start_vote_verifier_background_task(bot: Bot):
    """Orqa fonda har 30 soniyada kutilayotgan ovozlarni raqamma-raqam tekshirib boruvchi doimiy xizmat"""
    logger.info("Open Budget individual raqamli ovozlarni tasdiqlash fon xizmati ishga tushdi...")
    # Dastlabki startda 10 soniya kutamiz
    await asyncio.sleep(10)
    
    while True:
        try:
            await verify_pending_votes_step(bot)
        except asyncio.CancelledError:
            logger.info("Vote verifier fon xizmati to'xtatildi.")
            break
        except Exception as e:
            logger.error(f"Vote verifier asosiy tsiklida xatolik: {e}")
        
        await asyncio.sleep(30)
