import re
import html
import json
import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter

from sqlalchemy import update
from config import settings
from database.session import async_session
from database import crud
from database.models import VoteStatus, User
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

@router.message(F.text.contains("Ovoz berish"), StateFilter("*"))
async def start_voting(message: Message, state: FSMContext):
    """Ovoz berish FSM oqimini boshlash"""
    await state.clear()
    
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        if not active_project:
            await message.answer("❌ Hozircha botga faol Open Budget loyihasi ulanmagan. Iltimos, keyinroq qayta urinib ko'ring.")
            return

    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await message.answer(
        "🗳️ <b>Ovoz berish bo'limi</b>\n\n"
        "Iltimos, pastdagi tugma orqali kontaktingizni yuboring yoki telefon raqamingizni kiriting:\n"
        "(Masalan: +998901234567)",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_vote")
async def process_menu_vote(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan ovoz berish FSM oqimini boshlash"""
    await state.clear()
    
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        if not active_project:
            await callback.message.answer("❌ Hozircha botga faol Open Budget loyihasi ulanmagan. Iltimos, keyinroq qayta urinib ko'ring.")
            await callback.answer()
            return

    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await callback.message.answer(
        "🗳️ <b>Ovoz berish bo'limi</b>\n\n"
        "Iltimos, pastdagi tugma orqali kontaktingizni yuboring yoki telefon raqamingizni kiriting:\n"
        "(Masalan: +998901234567)",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(VoteStates.WAITING_FOR_PHONE, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    # Kontakt raqamini ham tekshiramiz
    cleaned = "".join(filter(str.isdigit, phone))
    if not (cleaned.startswith("998") and len(cleaned) == 12) and len(cleaned) != 9:
        await message.answer(
            "❌ Kontaktdagi raqam O'zbek telefon raqami emas.\n"
            "Iltimos o'z raqamingizni kiriting: +998XXXXXXXXX"
        )
        return
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

def mask_phone_display(phone: str) -> str:
    cleaned = "".join(filter(str.isdigit, phone))
    if len(cleaned) == 12 and cleaned.startswith("998"):
        return f"+998 {cleaned[3:5]} *** ** {cleaned[-2:]}"
    elif len(cleaned) == 9:
        return f"+998 {cleaned[:2]} *** ** {cleaned[-2:]}"
    return phone

async def start_in_bot_registration(message: Message, state: FSMContext, phone: str, project_id: str, from_user):
    """Foydalanuvchi Open Budget'da ro'yxatdan o'tmagan bo'lsa, bot ichida ro'yxatdan o'tishni boshlaydi"""
    await state.update_data(
        phone_number=phone,
        project_id=project_id,
        reg_first_name=from_user.first_name or "",
        reg_last_name=from_user.last_name or ""
    )
    await state.set_state(VoteStates.REG_WAITING_NAME)
    
    full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() or "Fuqaro"
    await message.answer(
        "📋 <b>Siz Open Budget tizimida hali ro'yxatdan o'tmagansiz.</b>\n\n"
        "Saytga kirib ovora bo'lishingiz shart emas! Hozir bot ichida <b>10 soniyada</b> ro'yxatdan o'tamiz va ovozingizni qabul qilamiz:\n\n"
        "👤 <b>1-Qadam: Ism va Familiyangiz</b>\n"
        "Quyidagi tugma orqali Telegram ismingizni tasdiqlang yoki o'zingiz yozing:",
        reply_markup=inline.get_name_choice_keyboard(from_user.first_name or "Fuqaro", from_user.last_name),
        parse_mode="HTML"
    )

async def handle_phone_submission(message: Message, state: FSMContext, phone: str):
    """Telefon raqamini tekshirish va SMS yoki Captcha so'rovini yuborish"""
    clean_phone = clean_phone_number(phone)
    telegram_id = message.from_user.id

    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        if not active_project:
            await message.answer("❌ Hozircha botga faol Open Budget loyihasi ulanmagan. Iltimos, keyinroq qayta urinib ko'ring.")
            return
        project_id = active_project.project_id

        # 1. Ushbu raqam orqali ovoz berilganligini tekshirish
        already_voted = await crud.check_phone_voted(db, clean_phone, project_id)
        if already_voted:
            await message.answer(
                "❌ Bu raqam orqali joriy loyihaga allaqachon ovoz berilgan.\n"
                "Iltimos, boshqa telefon raqam kiriting:",
                reply_markup=reply.get_phone_keyboard()
            )
            return

        # 2. Ushbu foydalanuvchi boshqa raqam orqali allaqachon ovoz berganligini tekshirish
        prev_phone = await crud.get_user_successful_vote_phone(db, telegram_id, project_id)
        if prev_phone and clean_phone != clean_phone_number(prev_phone):
            masked = mask_phone_display(prev_phone)
            await message.answer(
                f"⚠️ <b>Sizning nomingizdagi ({masked}) raqami orqali ushbu mavsumda allaqachon ovoz berilgan.</b>\n\n"
                f"Qonun bo'yicha har bir fuqaro faqat <b>1 marta</b> ovoz bera oladi.",
                reply_markup=reply.get_user_menu(),
                parse_mode="HTML"
            )
            return

    waiting_msg = await message.answer("🔄 Portalga so'rov yuborilmoqda, kuting...")

    # Portalga SMS so'rovi (Captcha kodi hali yuborilmagan)
    success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
        phone_number=clean_phone,
        project_id=project_id,
        captcha_key=None,
        captcha_result=None
    )
    
    await waiting_msg.delete()

    if not success:
        # A) Captcha talab etiladigan holat
        if error_msg == "captcha_required":
            # Captcha ma'lumotlarini yuklaymiz
            success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
            if not success_cap or not cap_data:
                await message.answer("❌ Captcha yuklab bo'lmadi. Qayta urinib ko'ring.")
                return

            await state.update_data(
                captcha_key=cap_data.get("key"),
                captcha_image=cap_data.get("image_base64"),
                phone_number=clean_phone,
                project_id=project_id,
                session_data=session_data,
            )
            await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
            web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
            session_id = str(telegram_id)
            await message.answer(
                "⚠️ <b>Xavfsizlik tekshiruvi (Captcha)!</b>\n\n"
                "Ovoz berishni davom ettirish uchun pastdagi tugmani bosing va captchani yeching 👇",
                reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
                parse_mode="HTML"
            )
            return

        # B) Foydalanuvchi ro'yxatdan o'tmagan bo'lsa -> Bot ichida ro'yxatdan o'tkazishni boshlash!
        elif error_msg == "not_registered" or "topilmadi" in error_msg.lower() or "foydalanuvchi" in error_msg.lower():
            await start_in_bot_registration(message, state, clean_phone, project_id, message.from_user)
            return

        # C) Allaqachon ovoz berilgan holat
        elif error_msg == "already_voted" or "allaqachon ovoz berilgan" in error_msg.lower():
            async with async_session() as db:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=clean_phone,
                    project_id=project_id,
                    status=VoteStatus.ALREADY_VOTED
                )
            await message.answer(
                "❌ Ushbu fuqaro / telefon raqami orqali Open Budget portalida allaqachon ovoz berilgan.\n"
                "Qonun bo'yicha har bir fuqaro faqat 1 marta ovoz bera oladi.",
                reply_markup=reply.get_user_menu()
            )
            return

        # D) Server xatoligi
        elif error_msg == "server_error":
            await message.answer(
                "⚠️ <b>Portal vaqtincha ishlamayapti.</b>\n\n"
                "openbudget.uz serveri xatolik qaytardi. "
                "Bir necha soniyadan so'ng qayta urinib ko'ring 🔁",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="HTML"
            )
            return

        # E) Boshqa xatoliklar yuz berganda
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
                f"❌ Xatolik yuz berdi:\n<b>{html.escape(error_msg)}</b>\n\n"
                f"Iltimos, qayta urunib ko'ring yoki boshqa raqam kiriting:",
                reply_markup=reply.get_phone_keyboard(),
                parse_mode="HTML"
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
        f"📩 <b>SMS yuborildi!</b>\n\n"
        f"<code>{clean_phone}</code> raqamiga yuborilgan 6 xonali SMS kodni kiriting:",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
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

        captcha_key = data.get("captcha_key") or "mock_captcha_key"
        captcha_result_str = data.get("captcha_result")
        
        try:
            captcha_result = int(captcha_result_str) if captcha_result_str is not None else 0
        except ValueError:
            captcha_result = 0

        state_data = await state.get_data()
        phone_number = state_data.get("phone_number")
        project_id = state_data.get("project_id")
        telegram_id = message.from_user.id

        waiting_msg = await message.answer("🔄 Captcha tasdiqlandi. SMS kod so'ralmoqda...")

        # Captcha kodi bilan portalga SMS so'rovi yuboramiz
        success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
            phone_number=phone_number,
            project_id=project_id,
            captcha_key=captcha_key,
            captcha_result=captcha_result
        )
        
        await waiting_msg.delete()

        if not success:
            if error_msg == "server_error":
                await message.answer(
                    "⚠️ <b>Portal vaqtincha ishlamayapti.</b>\n\n"
                    "openbudget.uz serveri xatolik qaytardi. "
                    "Bir necha soniyadan so'ng qayta urinib ko'ring 🔁",
                    reply_markup=reply.get_cancel_keyboard(),
                    parse_mode="HTML"
                )
            elif error_msg == "not_registered" or "topilmadi" in error_msg.lower() or "foydalanuvchi" in error_msg.lower():
                await start_in_bot_registration(message, state, phone_number, project_id, message.from_user)
            elif error_msg == "already_voted" or "allaqachon ovoz berilgan" in error_msg.lower():
                await message.answer(
                    "❌ Ushbu fuqaro / telefon raqami orqali Open Budget portalida allaqachon ovoz berilgan.\n"
                    "Qonun bo'yicha har bir fuqaro faqat 1 marta ovoz bera oladi.",
                    reply_markup=reply.get_user_menu()
                )
                await state.clear()
            else:
                await message.answer(
                    f"❌ SMS yuborishda xato yuz berdi:\n<b>{html.escape(error_msg)}</b>\n\n"
                    f"Qayta urinib ko'ring yoki bekor qiling.",
                    reply_markup=reply.get_cancel_keyboard(),
                    parse_mode="HTML"
                )
            return

        # SMS kod muvaffaqiyatli ketdi
        await state.update_data(session_data=session_data)
        await state.set_state(VoteStates.WAITING_FOR_SMS)
        
        await message.answer(
            f"📩 <b>Muvaffaqiyatli!</b>\n\n"
            f"<code>{phone_number}</code> raqamiga yuborilgan SMS tasdiqlash kodini kiriting:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
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
        session_data=session_data
    )

    await waiting_msg.delete()

    if not success:
        await message.answer(
            f"❌ Kod tasdiqlanmadi!\n<b>{html.escape(str(result_msg))}</b>\n\n"
            f"Iltimos, SMS kodni qaytadan kiriting yoki jarayonni bekor qiling:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # SMS kod tasdiqlandi, endi final captcha yuklaymiz
    access_token = result_msg  # verify_sms_code muvaffaqiyatli bo'lsa token qaytaradi
    
    # 2-captcha yuklash
    cap_waiting = await message.answer("🔄 Yakuniy xavfsizlik tekshiruvi (Captcha) yuklanmoqda...")
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    await cap_waiting.delete()

    if not success_cap or not cap_data:
        await message.answer("❌ Ovoz berish captchasini yuklab bo'lmadi. Iltimos, keyinroq qayta urinib ko'ring.")
        return

    # Foydalanuvchiga final captcha yechishni so'raymiz
    await state.update_data(
        access_token=access_token,
        captcha_key=cap_data.get("key"),
        captcha_image=cap_data.get("image_base64"),
    )
    await state.set_state(VoteStates.WAITING_FOR_FINAL_CAPTCHA)

    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    session_id = str(telegram_id)
    
    await message.answer(
        "🔒 <b>SMS kod tasdiqlandi!</b>\n\n"
        "Ovoz berishni yakunlash uchun pastdagi tugmani bosing va 2-captchani yeching 👇",
        reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
        parse_mode="HTML"
    )
    return
@router.message(VoteStates.WAITING_FOR_FINAL_CAPTCHA, F.web_app_data)
async def process_final_captcha_result(message: Message, state: FSMContext):
    """Foydalanuvchi final captchani yechganida ishlaydi (ovoz berishni yakunlash)"""
    try:
        raw_data = message.web_app_data.data
        data = json.loads(raw_data)
        
        if data.get("status") != "success":
            await message.answer("❌ Captcha tasdiqlanmadi. Iltimos, tugmani bosib qaytadan yeching.")
            return

        captcha_key = data.get("captcha_key") or "mock_captcha_key"
        captcha_result_str = data.get("captcha_result")
        
        try:
            captcha_result = int(captcha_result_str) if captcha_result_str is not None else 0
        except ValueError:
            captcha_result = 0

        state_data = await state.get_data()
        phone_number = state_data.get("phone_number")
        project_id = state_data.get("project_id")
        access_token = state_data.get("access_token")
        telegram_id = message.from_user.id

        waiting_msg = await message.answer("🔄 Ovoz berish yakunlanmoqda, kuting...")

        # Ovoz berish so'rovini yuboramiz
        success, result_msg = await OpenBudgetService.cast_vote(
            project_id=project_id,
            access_token=access_token,
            captcha_key=captcha_key,
            captcha_result=captcha_result
        )
        
        await waiting_msg.delete()

        if not success:
            if result_msg == "invalid_captcha":
                # Captcha noto'g'ri bo'lsa, qaytadan captcha ko'rsatamiz
                await message.answer("❌ Captcha noto'g'ri yechildi. Iltimos, yangisini yechib ko'ring:")
                
                cap_waiting = await message.answer("🔄 Yangi captcha yuklanmoqda...")
                success_cap, _, cap_data = await OpenBudgetService.get_captcha()
                await cap_waiting.delete()

                if not success_cap or not cap_data:
                    await message.answer("❌ Captcha yuklab bo'lmadi. Keyinroq qayta urinib ko'ring.")
                    return

                await state.update_data(
                    captcha_key=cap_data.get("key"),
                    captcha_image=cap_data.get("image_base64"),
                )
                web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
                session_id = str(telegram_id)
                await message.answer(
                    "🧩 <b>Yangi Captcha!</b>\n\n"
                    "Ovoz berishni yakunlash uchun pastdagi tugmani bosing 👇",
                    reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
                    parse_mode="HTML"
                )
                return
            
            elif result_msg == "already_voted":
                async with async_session() as db:
                    await crud.add_vote_history(
                        db=db,
                        telegram_id=telegram_id,
                        phone_number=phone_number,
                        project_id=project_id,
                        status=VoteStatus.ALREADY_VOTED
                    )
                await message.answer(
                    "❌ Ovoz berish rad etildi:\n<b>Bu raqam orqali ushbu loyihaga allaqachon ovoz berilgan.</b>",
                    reply_markup=reply.get_user_menu(),
                    parse_mode="HTML"
                )
                await state.clear()
                return

            else:
                # Muvaqqat portal yoki server xatoligi yuz berganda (masalan, 502/504)
                if any(x in result_msg.lower() for x in ["server", "portal", "ulanish", "timeout", "status: 5"]):
                    await message.answer(
                        f"⚠️ <b>Portalda vaqtincha xatolik yuz berdi:</b>\n{html.escape(result_msg)}\n\n"
                        f"Qaytadan urinib ko'rishingiz mumkin. Yangi captcha yuklanmoqda...",
                        parse_mode="HTML"
                    )
                    
                    cap_waiting = await message.answer("🔄 Yangi captcha yuklanmoqda...")
                    success_cap, _, cap_data = await OpenBudgetService.get_captcha()
                    await cap_waiting.delete()

                    if success_cap and cap_data:
                        await state.update_data(
                            captcha_key=cap_data.get("key"),
                            captcha_image=cap_data.get("image_base64"),
                        )
                        web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
                        session_id = str(telegram_id)
                        await message.answer(
                            "🧩 <b>Yangi Captcha!</b>\n\n"
                            "Qaytadan urinib ko'rish uchun pastdagi tugmani bosing 👇",
                            reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
                            parse_mode="HTML"
                        )
                        return

                # Boshqa jiddiy/doimiy rad etish holatlari
                async with async_session() as db:
                    await crud.add_vote_history(
                        db=db,
                        telegram_id=telegram_id,
                        phone_number=phone_number,
                        project_id=project_id,
                        status=VoteStatus.FAILED
                    )
                await message.answer(
                    f"❌ Ovoz berish yakunlanmadi:\n<b>{html.escape(result_msg)}</b>",
                    reply_markup=reply.get_user_menu(),
                    parse_mode="HTML"
                )
                await state.clear()
                return

        # --- OVOZ MUVAFFAQIYATLI QABUL QILINDI ---
        async with async_session() as db:
            try:
                # Ovoz tarixini bazaga yozamiz (commit qilmasdan, tranzaksiyada saqlaymiz)
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=phone_number,
                    project_id=project_id,
                    status=VoteStatus.SUCCESS,
                    commit=False
                )

                project_settings = await crud.get_project_settings(db)
                referral_price = project_settings.referral_price
                voter_reward = project_settings.voter_reward

                user = await crud.get_user(db, telegram_id)
                if user:
                    # Ovoz bergan odamning o'ziga mukofot (atomik)
                    if voter_reward > 0:
                        await db.execute(
                            update(User)
                            .where(User.telegram_id == telegram_id)
                            .values(balance=User.balance + voter_reward)
                        )
                    
                    # Taklif qilgan referalga mukofot (atomik)
                    if user.invited_by and referral_price > 0:
                        await db.execute(
                            update(User)
                            .where(User.telegram_id == user.invited_by)
                            .values(
                                balance=User.balance + referral_price,
                                total_referrals=User.total_referrals + 1
                            )
                        )
                        referrer = await crud.get_user(db, user.invited_by)
                        if referrer:
                            try:
                                referrer_message_text = (
                                    f"🎉 <b>Yangi referal mukofoti!</b>\n\n"
                                    f"Siz taklif qilgan foydalanuvchi ({html.escape(str(user.username or telegram_id))}) muvaffaqiyatli ovoz berdi.\n"
                                    f"💵 Balansingizga <b>{referral_price} so'm</b> qo'shildi!"
                                )
                                await message.bot.send_message(
                                    chat_id=referrer.telegram_id,
                                    text=referrer_message_text,
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(f"Refererga xabar yuborishda xato: {e}")

                # Barcha o'zgarishlarni bitta tranzaksiyada tasdiqlaymiz (Atomicity)
                await db.commit()
                
                await message.answer(
                    f"<tg-emoji emoji-id='5472164874884394982'>✅</tg-emoji> <b>TABRIKLAYMIZ! Ovoz muvaffaqiyatli qabul qilindi!</b>\n\n"
                    f"<tg-emoji emoji-id='5471971711481666499'>💰</tg-emoji> Balansingizga: <b>+{voter_reward:,} so'm</b> qo'shildi!\n\n"
                    f"Do'stlaringizni taklif qiling va ko'proq daromad oling! <tg-emoji emoji-id='5471987512674727448'>👥</tg-emoji>",
                    reply_markup=reply.get_user_menu(),
                    parse_mode="HTML"
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

    except Exception as e:
        logger.error(f"Final Captcha WebApp natijasini o'qishda xato: {e}", exc_info=True)
        await message.answer("❌ Captcha ma'lumotlarini qabul qilishda xatolik yuz berdi. Qayta urinib ko'ring.")

# ==========================================
# RO'YXATDAN O'TISH (IN-BOT REGISTRATION) HANDLERLARI
# ==========================================

@router.callback_query(F.data == "reg_cancel")
async def process_reg_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
    await callback.answer()

@router.callback_query(F.data == "reg_use_tg_name", VoteStates.REG_WAITING_NAME)
async def process_reg_use_tg_name(callback: CallbackQuery, state: FSMContext):
    """Telegram profilidagi ism-familiyani tasdiqlash"""
    from_user = callback.from_user
    first_name = from_user.first_name or "Fuqaro"
    last_name = from_user.last_name or ""
    
    await state.update_data(reg_first_name=first_name, reg_last_name=last_name)
    await state.set_state(VoteStates.REG_WAITING_BIRTHDAY)
    
    await callback.message.answer(
        f"✅ <b>Ism:</b> {first_name} {last_name}\n\n"
        f"📅 <b>2-Qadam: Tug'ilgan sanangiz</b>\n"
        f"Iltimos, tug'ilgan sanangizni kiriting:\n"
        f"(Masalan: <code>15.06.1998</code> yoki shunchaki yilingiz <code>1998</code>)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "reg_custom_name", VoteStates.REG_WAITING_NAME)
async def process_reg_custom_name_btn(callback: CallbackQuery, state: FSMContext):
    """Boshqa ism yozishni tanlaganda"""
    await callback.message.answer(
        "✏️ Iltimos, Ism va Familiyangizni yozing:\n"
        "(Masalan: <i>Jahongir Tojiboyev</i>)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(VoteStates.REG_WAITING_NAME, F.text)
async def process_reg_custom_name_text(message: Message, state: FSMContext):
    """Foydalanuvchi ism-familiyani matn sifatida yuborganda"""
    text = message.text.strip()
    if text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    parts = text.split(maxsplit=1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    await state.update_data(reg_first_name=first_name, reg_last_name=last_name)
    await state.set_state(VoteStates.REG_WAITING_BIRTHDAY)
    
    await message.answer(
        f"✅ <b>Ism:</b> {first_name} {last_name}\n\n"
        f"📅 <b>2-Qadam: Tug'ilgan sanangiz</b>\n"
        f"Iltimos, tug'ilgan sanangizni kiriting:\n"
        f"(Masalan: <code>15.06.1998</code> yoki shunchaki yilingiz <code>1998</code>)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(VoteStates.REG_WAITING_BIRTHDAY, F.text)
async def process_reg_birthday(message: Message, state: FSMContext):
    """Tug'ilgan sanani qabul qilish va formatlash"""
    text = message.text.strip()
    if text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    # Sana formatlash (YYYY-MM-DD)
    clean_date = "1998-01-01"
    digits = re.findall(r'\d+', text)
    
    if len(digits) == 1 and len(digits[0]) == 4:
        # Faqat yil kiritilgan (masalan: 1998)
        year = int(digits[0])
        if 1940 <= year <= 2010:
            clean_date = f"{year}-01-01"
        else:
            clean_date = "1998-01-01"
    elif len(digits) >= 3:
        # DD.MM.YYYY yoki YYYY-MM-DD
        if len(digits[0]) == 4: # YYYY-MM-DD
            clean_date = f"{digits[0]}-{digits[1].zfill(2)}-{digits[2].zfill(2)}"
        else: # DD-MM-YYYY
            clean_date = f"{digits[2]}-{digits[1].zfill(2)}-{digits[0].zfill(2)}"

    await state.update_data(reg_birth_date=clean_date)
    await state.set_state(VoteStates.REG_WAITING_GENDER)

    await message.answer(
        "👤 <b>3-Qadam: Jinsingizni tanlang:</b>",
        reply_markup=inline.get_gender_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("reg_gender_"), VoteStates.REG_WAITING_GENDER)
async def process_reg_gender(callback: CallbackQuery, state: FSMContext):
    """Jinsni tanlash"""
    gender = callback.data.replace("reg_gender_", "")
    await state.update_data(reg_gender=gender)
    await state.set_state(VoteStates.REG_WAITING_REGION)

    gender_text = "👨 Erkak" if gender == "MALE" else "👩 Ayol"
    await callback.message.answer(
        f"✅ <b>Jins:</b> {gender_text}\n\n"
        f"📍 <b>4-Qadam: Viloyatingizni tanlang:</b>",
        reply_markup=inline.get_regions_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "reg_back_regions", VoteStates.REG_WAITING_DISTRICT)
async def process_reg_back_regions(callback: CallbackQuery, state: FSMContext):
    """Viloyatni qaytadan tanlash"""
    await state.set_state(VoteStates.REG_WAITING_REGION)
    await callback.message.answer(
        "📍 <b>Viloyatingizni tanlang:</b>",
        reply_markup=inline.get_regions_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reg_region_"), VoteStates.REG_WAITING_REGION)
async def process_reg_region(callback: CallbackQuery, state: FSMContext):
    """Viloyat tanlanganda uning tumanlarini chiqarish"""
    from utils.regions import get_region_name
    region_id = int(callback.data.replace("reg_region_", ""))
    await state.update_data(reg_region_id=region_id)
    await state.set_state(VoteStates.REG_WAITING_DISTRICT)

    region_name = get_region_name(region_id)
    await callback.message.answer(
        f"📍 <b>Viloyat:</b> {region_name}\n\n"
        f"🏙️ <b>5-Qadam: Tumaningizni tanlang:</b>",
        reply_markup=inline.get_districts_keyboard(region_id),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reg_dist_"), VoteStates.REG_WAITING_DISTRICT)
async def process_reg_district(callback: CallbackQuery, state: FSMContext):
    """Tuman tanlangach, ro'yxatdan o'tish so'rovini avtomatik portalga yuborish"""
    parts = callback.data.split("_")
    region_id = int(parts[2])
    district_id = int(parts[3])
    
    state_data = await state.get_data()
    phone_number = state_data.get("phone_number")
    project_id = state_data.get("project_id")
    first_name = state_data.get("reg_first_name") or "Fuqaro"
    last_name = state_data.get("reg_last_name") or ""
    birth_date = state_data.get("reg_birth_date") or "1998-01-01"
    gender = state_data.get("reg_gender") or "MALE"
    telegram_id = callback.from_user.id

    waiting_msg = await callback.message.answer("🔄 <b>Open Budget'ga ro'yxatdan o'tish so'rovi yuborilmoqda...</b>", parse_mode="HTML")
    await callback.answer()

    # 1. Captcha olish
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    captcha_key = cap_data.get("key") if cap_data else None
    captcha_result = 9 # Avtomatik matematik captcha yechimi

    # 2. Ro'yxatdan o'tish OTP so'rovi
    success, result_msg, session_data = await OpenBudgetService.send_registration_otp(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
        gender=gender,
        birth_date=birth_date,
        region_id=region_id,
        district_id=district_id,
        project_id=project_id,
        captcha_key=captcha_key,
        captcha_result=captcha_result,
        profession="Xodim"
    )

    await waiting_msg.delete()

    if not success:
        await callback.message.answer(
            f"❌ <b>Ro'yxatdan o'tishda xatolik:</b>\n{html.escape(result_msg)}\n\n"
            f"Iltimos, qaytadan urinib ko'ring yoki boshqa raqam kiriting:",
            reply_markup=reply.get_phone_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    # SMS muvaffaqiyatli ketdi
    await state.update_data(session_data=session_data)
    await state.set_state(VoteStates.REG_WAITING_SMS)

    await callback.message.answer(
        f"📩 <b>SMS yuborildi!</b>\n\n"
        f"<code>{phone_number}</code> raqamiga yuborilgan 6 xonali SMS kodni kiriting:\n\n"
        f"<i>Kodni kiritishingiz bilan hisobingiz ochiladi va ovozingiz qabul qilinadi!</i> ⚡",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(VoteStates.REG_WAITING_SMS, F.text)
async def process_reg_sms_code(message: Message, state: FSMContext):
    """Ro'yxatdan o'tish SMS kodini tekshirish va darhol ovoz berish jarayoniga o'tish"""
    code = message.text.strip()
    if code == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    if not code.isdigit():
        await message.answer("❌ SMS kod faqat raqamlardan iborat bo'lishi kerak. Iltimos to'g'ri kodni kiriting:")
        return

    state_data = await state.get_data()
    phone_number = state_data.get("phone_number")
    project_id = state_data.get("project_id")
    session_data = state_data.get("session_data")
    telegram_id = message.from_user.id

    waiting_msg = await message.answer("🔄 <b>Ro'yxatdan o'tish tasdiqlanmoqda...</b>", parse_mode="HTML")

    success, result_msg = await OpenBudgetService.verify_registration_otp(
        phone_number=phone_number,
        code=code,
        session_data=session_data
    )

    await waiting_msg.delete()

    if not success:
        await message.answer(
            f"❌ <b>Tasdiqlashda xatolik:</b>\n{html.escape(result_msg)}\n\n"
            f"Iltimos, SMS kodni qaytadan kiriting:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    access_token = result_msg

    # Muvaffaqiyatli ro'yxatdan o'tildi! Endi ovoz berish uchun final captcha yuklaymiz
    cap_waiting = await message.answer("✅ <b>Ro'yxatdan o'tish muvaffaqiyatli yakunlandi!</b>\n🔄 Ovoz berish uchun Captcha yuklanmoqda...", parse_mode="HTML")
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    await cap_waiting.delete()

    if not success_cap or not cap_data:
        await message.answer("❌ Captcha yuklab bo'lmadi. Keyinroq qayta urinib ko'ring.")
        return

    await state.update_data(
        access_token=access_token,
        captcha_key=cap_data.get("key"),
        captcha_image=cap_data.get("image_base64"),
    )
    await state.set_state(VoteStates.WAITING_FOR_FINAL_CAPTCHA)

    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    session_id = str(telegram_id)

    await message.answer(
        "🎉 <b>Hisobingiz ochildi!</b>\n\n"
        "Endi ovoz berishni yakunlash uchun pastdagi tugmani bosing va yakuniy captchani yeching 👇",
        reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
        parse_mode="HTML"
    )



