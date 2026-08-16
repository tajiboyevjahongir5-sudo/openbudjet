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

        # C) Server xatoligi
        elif error_msg == "server_error":
            await message.answer(
                "⚠️ <b>Portal vaqtincha ishlamayapti.</b>\n\n"
                "openbudget.uz serveri xatolik qaytardi. "
                "Bir necha soniyadan so'ng qayta urinib ko'ring 🔁",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="HTML"
            )
            return

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
            
            # Raqam ro'yxatdan o'tmagan bo'lsa batafsil ko'rsatma berish va tekshirish holatiga o'tkazish
            if "topilmadi" in error_msg.lower() or "foydalanuvchi" in error_msg.lower():
                await state.update_data(
                    phone_number=clean_phone,
                    project_id=project_id,
                    is_checking_registration=True
                )
                await state.set_state(VoteStates.WAITING_FOR_REGISTRATION_CHECK)
                await message.answer(
                    f"⚠️ <b>Raqam ro'yxatdan o'tmagan!</b>\n\n"
                    f"Kiritilgan <code>{clean_phone}</code> raqami Open Budget (OneID) tizimida ro'yxatdan o'tmagan. "
                    f"SMS kod yuborilishi uchun ushbu raqam kamida 1 marta OneID'dan o'tgan bo'lishi kerak.\n\n"
                    f"🛠️ <b>Muammoni hal qilish yo'li:</b>\n"
                    f"1. Fuqaro o'z telefonidan <a href='https://id.egov.uz'>id.egov.uz</a> saytiga kiradi.\n"
                    f"2. <b>'Ro'yxatdan o'tish'</b> tugmasini bosib, pasporti va ushbu raqamini kiritib tasdiqlaydi (1 daqiqa vaqt oladi).\n\n"
                    f"Ushbu amal bajarilgach, quyidagi <b>'🔄 Ro'yxatdan o'tdim, tekshirish'</b> tugmasini bosing:",
                    reply_markup=reply.get_check_registration_keyboard(),
                    parse_mode="HTML"
                )
            else:
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
            elif "topilmadi" in error_msg.lower() or "foydalanuvchi" in error_msg.lower():
                await state.update_data(
                    phone_number=phone_number,
                    project_id=project_id,
                    is_checking_registration=True
                )
                await state.set_state(VoteStates.WAITING_FOR_REGISTRATION_CHECK)
                await message.answer(
                    f"⚠️ <b>Raqam ro'yxatdan o'tmagan!</b>\n\n"
                    f"Kiritilgan <code>{phone_number}</code> raqami Open Budget (OneID) tizimida ro'yxatdan o'tmagan. "
                    f"SMS kod yuborilishi uchun ushbu raqam kamida 1 marta OneID'dan o'tgan bo'lishi kerak.\n\n"
                    f"🛠️ <b>Muammoni hal qilish yo'li:</b>\n"
                    f"1. Fuqaro o'z telefonidan <a href='https://id.egov.uz'>id.egov.uz</a> saytiga kiradi.\n"
                    f"2. <b>'Ro'yxatdan o'tish'</b> tugmasini bosib, pasporti va ushbu raqamini kiritib tasdiqlaydi (1 daqiqa vaqt oladi).\n\n"
                    f"Ushbu amal bajarilgach, quyidagi <b>'🔄 Ro'yxatdan o'tdim, tekshirish'</b> tugmasini bosing:",
                    reply_markup=reply.get_check_registration_keyboard(),
                    parse_mode="HTML"
                )
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
        
        is_checking = state_data.get("is_checking_registration", False)
        if is_checking:
            await message.answer(
                f"✅ <b>Ro'yxatdan o'tish muvaffaqiyatli bo'ldi!</b> SMS yuborildi.\n\n"
                f"<code>{phone_number}</code> raqamiga yuborilgan SMS tasdiqlash kodini kiriting:",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="HTML"
            )
            await state.update_data(is_checking_registration=False)
        else:
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

@router.message(VoteStates.WAITING_FOR_REGISTRATION_CHECK)
async def check_registration_status(message: Message, state: FSMContext):
    """Foydalanuvchi OneID'dan ro'yxatdan o'tib, 'Tekshirish' tugmasini bosganda ishlaydi"""
    text = message.text.strip()
    if text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return
        
    if text == "🔄 Ro'yxatdan o'tdim, tekshirish":
        telegram_id = message.from_user.id
        
        # Yangi captcha yuklaymiz
        cap_waiting = await message.answer("🔄 Yangi captcha yuklanmoqda...")
        success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
        await cap_waiting.delete()
        
        if not success_cap or not cap_data:
            await message.answer("❌ Captcha yuklab bo'lmadi. Qayta urinib ko'ring.")
            return

        # Captcha ma'lumotlarini saqlaymiz va WAITING_FOR_CAPTCHA holatiga qaytaramiz
        await state.update_data(
            captcha_key=cap_data.get("key"),
            captcha_image=cap_data.get("image_base64")
        )
        await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
        
        web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
        session_id = str(telegram_id)
        
        await message.answer(
            "🔄 <b>Ro'yxatdan o'tganlik tekshirilmoqda...</b>\n\n"
            "Iltimos, pastdagi tugma orqali captchani yeching. "
            "Agar muvaffaqiyatli ro'yxatdan o'tgan bo'lsangiz, raqamingizga SMS yuboriladi 👇",
            reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "Iltimos, quyidagi tugmalardan birini bosing 👇",
            reply_markup=reply.get_check_registration_keyboard()
        )


