import re
import json
import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter

from config import settings
from database.session import async_session
from database import crud
from database.models import VoteStatus
from states.user_states import VoteStates
from keyboards import reply, inline
from services.openbudget import OpenBudgetService

router = Router()
logger = logging.getLogger(__name__)

# Telefon raqamlarini formatlash uchun regex
PHONE_REGEX = re.compile(r"^\+?(998)?\s?\(?\d{2}\)?\s?\d{3}\s?\d{2}\s?\d{2}$")

def clean_phone_number(phone: str) -> str:
    """Telefon raqamidan faqat raqamlarni ajratib oladi va 998 prefiksini qo'shadi"""
    digits = "".join(filter(str.isdigit, phone))
    if len(digits) == 9:
        digits = f"998{digits}"
    elif digits.startswith("8") and len(digits) == 11:
        digits = f"998{digits[2:]}"
    return digits

@router.message(F.text == "⚡ Ovoz berish")
async def start_voting(message: Message, state: FSMContext):
    """Ovoz berish FSM oqimini boshlash"""
    await state.clear()
    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await message.answer(
        "🗳️ **Ovoz berish bo'limi**\n\n"
        "Iltimos, pastdagi tugma orqali kontaktingizni yuboring yoki telefon raqamingizni kiriting:\n"
        "(Masalan: +998901234567)",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "menu_vote")
async def process_menu_vote(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan ovoz berish FSM oqimini boshlash"""
    await state.clear()
    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await callback.message.answer(
        "🗳️ **Ovoz berish bo'limi**\n\n"
        "Iltimos, pastdagi tugma orqali kontaktingizni yuboring yoki telefon raqamingizni kiriting:\n"
        "(Masalan: +998901234567)",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(VoteStates.WAITING_FOR_PHONE, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await handle_phone_submission(message, state, phone)

@router.message(VoteStates.WAITING_FOR_PHONE, F.text)
async def process_phone_text(message: Message, state: FSMContext):
    phone = message.text.strip()
    if phone == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    if not PHONE_REGEX.match(phone):
        await message.answer(
            "❌ Telefon raqami formati noto'g'ri. Iltimos raqamni to'g'ri kiriting:\n"
            "Masalan: +998901234567 yoki tugma orqali kontaktingizni ulashing."
        )
        return

    await handle_phone_submission(message, state, phone)

async def handle_phone_submission(message: Message, state: FSMContext, phone: str):
    """Telefon raqamini tekshirish va SMS yoki Captcha so'rovini yuborish"""
    clean_phone = clean_phone_number(phone)
    telegram_id = message.from_user.id

    async with async_session() as db:
        project_settings = await crud.get_project_settings(db)
        project_id = project_settings.active_project_id

        already_voted = await crud.check_phone_voted(db, clean_phone, project_id)
        if already_voted:
            await message.answer(
                "❌ Bu raqam orqali joriy loyihaga allaqachon ovoz berilgan.\n"
                "Iltimos, boshqa telefon raqam kiriting:",
                reply_markup=reply.get_phone_keyboard()
            )
            return

    waiting_msg = await message.answer("🔄 Portalga so'rov yuborilmoqda, kuting...")

    # Portalga SMS so'rovi (Captcha kodi hali yuborilmagan)
    success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
        phone_number=clean_phone,
        project_id=project_id,
        captcha_code=None
    )
    
    await waiting_msg.delete()

    if not success:
        # A) Captcha talab etiladigan holat (Gibrid usul ishga tushadi)
        if error_msg == "captcha_required":
            session_id = session_data.get("mock_session_id") or session_data.get("session_token") or "session"
            
            # Web App uchun manzilni aniqlash
            web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
            
            await state.update_data(
                phone_number=clean_phone,
                project_id=project_id,
                session_data=session_data
            )
            await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
            
            await message.answer(
                "⚠️ **Xavfsizlik tekshiruvi (Captcha)!**\n\n"
                "Ovoz berishni davom ettirish uchun pastdagi tugmani bosing va puzlni joylashtiring 👇",
                reply_markup=inline.get_captcha_keyboard(session_id, web_url),
                parse_mode="Markdown"
            )
            return

        # B) Allaqachon ovoz berilgan bo'lsa
        elif "allaqachon ovoz berilgan" in error_msg.lower():
            async with async_session() as db:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=clean_phone,
                    project_id=project_id,
                    status=VoteStatus.ALREADY_VOTED
                )
            await message.answer(
                "❌ Ushbu raqam orqali Open Budget portalida allaqachon ovoz berilgan.\n"
                "Iltimos, boshqa telefon raqam kiriting:",
                reply_markup=reply.get_phone_keyboard()
            )
        # C) Boshqa xatoliklar yuz berganda
        else:
            async with async_session() as db:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=clean_phone,
                    project_id=project_id,
                    status=VoteStatus.FAILED
                )
            await message.answer(
                f"❌ Xatolik yuz berdi:\n`{error_msg}`\n\n"
                f"Iltimos, qayta urunib ko'ring yoki boshqa raqam kiriting:",
                reply_markup=reply.get_phone_keyboard(),
                parse_mode="Markdown"
            )
        return

    # Captcha talab etilmasdan birdan SMS ketgan holat (agar portalda captcha o'chirilgan bo'lsa)
    await state.update_data(
        phone_number=clean_phone,
        project_id=project_id,
        session_data=session_data
    )
    await state.set_state(VoteStates.WAITING_FOR_SMS)
    await message.answer(
        f"📩 **SMS yuborildi!**\n\n"
        f"`{clean_phone}` raqamiga yuborilgan 6 xonali SMS kodni kiriting:",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(VoteStates.WAITING_FOR_CAPTCHA, F.web_app_data)
async def process_captcha_result(message: Message, state: FSMContext):
    """Foydalanuvchi Web App orqali captchani yechganida ishlaydi"""
    try:
        raw_data = message.web_app_data.data
        data = json.loads(raw_data)
        
        if data.get("status") != "success":
            await message.answer("❌ Captcha tasdiqlanmadi. Iltimos tugmani bosib, qaytadan yeching.")
            return

        captcha_code = data.get("captcha_code")
        state_data = await state.get_data()
        phone_number = state_data.get("phone_number")
        project_id = state_data.get("project_id")
        telegram_id = message.from_user.id

        waiting_msg = await message.answer("🔄 Captcha tasdiqlandi. SMS kod so'ralmoqda...")

        # Captcha kodi bilan portalga SMS so'rovi yuboramiz
        success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
            phone_number=phone_number,
            project_id=project_id,
            captcha_code=captcha_code
        )
        
        await waiting_msg.delete()

        if not success:
            await message.answer(
                f"❌ Captcha to'g'ri, lekin SMS yuborishda xato yuz berdi:\n`{error_msg}`\n\n"
                f"Qayta urinib ko'ring yoki bekor qiling.",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="Markdown"
            )
            return

        # SMS kod muvaffaqiyatli ketdi
        await state.update_data(session_data=session_data)
        await state.set_state(VoteStates.WAITING_FOR_SMS)
        await message.answer(
            f"📩 **Muvaffaqiyatli!**\n\n"
            f"`{phone_number}` raqamiga yuborilgan SMS tasdiqlash kodini kiriting:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Captcha WebApp natijasini o'qishda xato: {e}", exc_info=True)
        await message.answer("❌ Captcha ma'lumotlarini qabul qilishda xatolik yuz berdi. Qayta urinib ko'ring.")

@router.message(VoteStates.WAITING_FOR_SMS, F.text)
async def process_sms_code(message: Message, state: FSMContext):
    """SMS kodini tekshirish va muvaffaqiyatli bo'lsa foydalanuvchilarni mukofotlash"""
    code = message.text.strip()
    
    if code == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    if not code.isdigit():
        await message.answer("❌ SMS kod faqat raqamlardan iborat bo'lishi kerak. Iltimos to'g'ri kodni kiriting:")
        return

    data = await state.get_data()
    phone_number = data.get("phone_number")
    project_id = data.get("project_id")
    session_data = data.get("session_data")
    telegram_id = message.from_user.id

    waiting_msg = await message.answer("🔄 SMS kod tekshirilmoqda, kuting...")
    
    success, result_msg = await OpenBudgetService.verify_sms_code(
        phone_number=phone_number,
        code=code,
        project_id=project_id,
        session_data=session_data
    )

    await waiting_msg.delete()

    if not success:
        await message.answer(
            f"❌ Kod tasdiqlanmadi!\n`{result_msg}`\n\n"
            f"Iltimos, SMS kodni qaytadan kiriting yoki jarayonni bekor qiling:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="Markdown"
        )
        return

    # Ovoz muvaffaqiyatli tasdiqlandi. Balanslarni to'ldiramiz.
    async with async_session() as db:
        try:
            await crud.add_vote_history(
                db=db,
                telegram_id=telegram_id,
                phone_number=phone_number,
                project_id=project_id,
                status=VoteStatus.SUCCESS
            )

            project_settings = await crud.get_project_settings(db)
            referral_price = project_settings.referral_price

            user = await crud.get_user(db, telegram_id)
            if user:
                user.balance += referral_price
                
                # Referal tizimini mukofotlash
                if user.invited_by:
                    referrer = await crud.get_user(db, user.invited_by)
                    if referrer:
                        referrer.balance += referral_price
                        referrer.total_referrals += 1
                        
                        try:
                            referrer_message_text = (
                                f"🎉 **Yangi referal mukofoti!**\n\n"
                                f"Siz taklif qilgan foydalanuvchi ({user.username or telegram_id}) muvaffaqiyatli ovoz berdi.\n"
                                f"💵 Balansingizga **{referral_price} so'm** qo'shildi!"
                            )
                            await message.bot.send_message(
                                chat_id=referrer.telegram_id,
                                text=referrer_message_text,
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            logger.error(f"Refererga xabar yuborishda xato (ID: {referrer.telegram_id}): {e}")

            await db.commit()
            
            await message.answer(
                f"🎉 **Tabriklaymiz!** Ovoz muvaffaqiyatli qabul qilindi.\n"
                f"💵 Balansingizga **{referral_price} so'm** qo'shildi!",
                reply_markup=reply.get_user_menu(),
                parse_mode="Markdown"
            )
            await state.clear()

        except Exception as e:
            await db.rollback()
            logger.error(f"Ovoz yozish tranzaksiyasida xatolik: {e}", exc_info=True)
            await message.answer(
                "❌ Ovoz qabul qilindi, lekin ma'lumotlarni saqlashda xatolik yuz berdi.\n"
                "Iltimos, adminlar bilan bog'laning.",
                reply_markup=reply.get_user_menu()
            )
            await state.clear()
