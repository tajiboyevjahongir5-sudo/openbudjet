import re
import html
import json
import logging
import asyncio
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

PHONE_REGEX = re.compile(r"^\+?(998)?\s?\(?\d{2}\)?\s?\d{3}\s?\d{2}\s?\d{2}$")

def clean_phone_number(phone: str) -> str:
    """Telefon raqamidan faqat raqamlarni ajratib oladi va 998 prefiksini qo'shadi"""
    digits = "".join(filter(str.isdigit, phone))
    if len(digits) == 9:
        digits = f"998{digits}"
    elif digits.startswith("8") and len(digits) == 11:
        digits = f"998{digits[2:]}"
    return digits

_code_cache: dict[str, str] = {"0a70f4e1-0ca3-4407-8ac3-939cfa4a4653": "055529529012"}

@router.message(F.text.contains("Ovoz berish"), StateFilter("*"))
async def start_voting(message: Message, state: FSMContext):
    """Ovoz berish bo'limi - To'g'ridan-to'g'ri telefon raqam so'raydi"""
    await state.clear()
    
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        if not active_project:
            await message.answer("❌ Hozircha botga faol Open Budget loyihasi ulanmagan. Iltimos, keyinroq qayta urinib ko'ring.")
            return
        
        project_id = active_project.project_id
        settings_data = await crud.get_project_settings(db)
        voter_reward = settings_data.voter_reward

        display_code = _code_cache.get(str(project_id), "055529529012")

    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await message.answer(
        f"🗳️ <b>Open Budget loyihasiga ovoz berish</b>\n\n"
        f"📌 <b>Faol loyiha kodi:</b> <code>{display_code}</code>\n"
        f"💰 <b>Ovoz uchun mukofot:</b> <b>+{voter_reward:,.0f} so'm</b>\n\n"
        f"Iltimos, pastdagi tugma orqali kontaktingizni ulashing yoki telefon raqamingizni kiriting:\n"
        f"<i>(Masalan: +998901234567)</i>",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_vote")
async def process_menu_vote(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan ovoz berish - To'g'ridan-to'g'ri telefon raqam so'raydi"""
    await state.clear()
    
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        if not active_project:
            await callback.message.answer("❌ Hozircha botga faol Open Budget loyihasi ulanmagan. Iltimos, keyinroq qayta urinib ko'ring.")
            await callback.answer()
            return
        
        project_id = active_project.project_id
        settings_data = await crud.get_project_settings(db)
        voter_reward = settings_data.voter_reward

        display_code = _code_cache.get(str(project_id), "055529529012")

    await state.set_state(VoteStates.WAITING_FOR_PHONE)
    await callback.message.answer(
        f"🗳️ <b>Open Budget loyihasiga ovoz berish</b>\n\n"
        f"📌 <b>Faol loyiha kodi:</b> <code>{display_code}</code>\n"
        f"💰 <b>Ovoz uchun mukofot:</b> <b>+{voter_reward:,.0f} so'm</b>\n\n"
        f"Iltimos, pastdagi tugma orqali kontaktingizni ulashing yoki telefon raqamingizni kiriting:\n"
        f"<i>(Masalan: +998901234567)</i>",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(VoteStates.WAITING_FOR_PHONE, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
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
    """Foydalanuvchi Open Budget'da ro'yxatdan o'tmagan bo'lsa, bot o'zi avtomatik ro'yxatdan o'tkazadi"""
    import random

    uz_names = [
        "Jahongir Aliyev", "Sardor Karimov", "Madina Umarova",
        "Zulfiya Rashidova", "Bobur Yusupov", "Malika Nazarova",
        "Sherzod Hamidov", "Dilnoza Toshmatova", "Eldor Raxmatullayev",
        "Nilufar Xasanova", "Bahodir Sobirov", "Gulnora Mirzayeva",
        "Jasur Abdullayev", "Mushtariy Normatova", "Ulugbek Qodirov",
        "Feruza Holmatova", "Rustam Bekmurodov", "Oydin Yunusova",
        "Nodir Mamatov", "Barno Ergasheva"
    ]
    fullname = random.choice(uz_names)
    name_parts = fullname.split()
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""
    gender = random.choice(["MALE", "FEMALE"])
    year = random.randint(1970, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    birth_date = f"{year}-{month:02d}-{day:02d}"

    region_id = 1
    district_id = 1

    auto_reg_msg = await message.answer(
        "🤖 <b>Tizimda ro'yxatdan o'tmagansiz.</b>\n\n"
        "⏳ Bot sizni <b>avtomatik ravishda</b> ro'yxatdan o'tkazmoqda...\n"
        "🔄 Captcha yuklanmoqda...",
        parse_mode="HTML"
    )

    reg_success = False
    reg_result_msg = ""
    reg_session_data = None

    for reg_attempt in range(2):
        if reg_attempt > 0:
            try:
                await auto_reg_msg.edit_text(
                    f"🤖 <b>Avtomatik ro'yxatdan o'tkazilmoqda...</b>\n🔄 Yangi captcha olinmoqda ({reg_attempt+1}/2)...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
        if not success_cap or not cap_data:
            continue

        captcha_key = cap_data.get("key")
        captcha_image = cap_data.get("image_base64")

        captcha_result = None
        if captcha_image and not cap_data.get("mock"):
            try:
                await auto_reg_msg.edit_text(
                    "🤖 <b>Tizimda ro'yxatdan o'tmagansiz.</b>\n\n"
                    "⏳ Bot sizni <b>avtomatik ravishda</b> ro'yxatdan o'tkazmoqda...\n"
                    "🧠 Captcha avtomatik yechilmoqda...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            try:
                from services.captcha_solver import solve_captcha
                captcha_result = await solve_captcha(captcha_image)
            except Exception as e:
                logger.warning(f"Ro'yxatdan o'tish captcha avtomatik yechishda xato: {e}")

        if captcha_result is None:
            continue

        logger.info(f"Ro'yxatdan o'tish captchasi avtomatik yechildi ({reg_attempt+1}-urinish): {captcha_result}")

        try:
            await auto_reg_msg.edit_text(
                "🤖 <b>Tizimda ro'yxatdan o'tmagansiz.</b>\n\n"
                "⏳ Bot sizni <b>avtomatik ravishda</b> ro'yxatdan o'tkazmoqda...\n"
                "📩 SMS kod so'ralmoqda...",
                parse_mode="HTML"
            )
        except Exception:
            pass

        success, result_msg, session_data = await OpenBudgetService.send_registration_otp(
            first_name=first_name,
            last_name=last_name,
            phone_number=phone,
            gender=gender,
            birth_date=birth_date,
            region_id=region_id,
            district_id=district_id,
            project_id=project_id,
            captcha_key=captcha_key,
            captcha_result=captcha_result,
            profession="Xodim"
        )

        if success:
            reg_success = True
            reg_session_data = session_data
            break
        else:
            reg_result_msg = result_msg
            logger.warning(f"Ro'yxatdan o'tish OTP xatosi ({reg_attempt+1}-urinish): {result_msg}")

    try:
        await auto_reg_msg.delete()
    except Exception:
        pass

    if not reg_success:
        logger.warning(f"Avtomatik ro'yxatdan o'tishda xatolik: {reg_result_msg}")
        await message.answer(
            f"❌ <b>Ro'yxatdan o'tishda xatolik:</b>\n{html.escape(str(reg_result_msg or 'Captcha xatosi'))}\n\n"
            f"Iltimos, qaytadan urinib ko'ring yoki boshqa raqam kiriting:",
            reply_markup=reply.get_phone_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    await state.update_data(session_data=reg_session_data, phone_number=phone, project_id=project_id)
    await state.set_state(VoteStates.REG_WAITING_SMS)
    await message.answer(
        f"✅ <b>Ro'yxatdan o'tish muvaffaqiyatli boshlandi!</b>\n\n"
        f"📩 <code>{phone}</code> raqamiga <b>6 xonali SMS kod</b> yuborildi.\n"
        f"Kodni pastga kiriting:\n\n"
        f"<i>⚡ Kodni kiritishingiz bilan hisobingiz ochiladi va ovozingiz qabul qilinadi!</i>",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

async def handle_phone_submission(message: Message, state: FSMContext, phone: str):
    """Telefon raqamini tekshirish va SMS so'rovini yuborish"""
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

    waiting_msg = await message.answer("⏳ <b>Bog'lanish:</b> Loyihaga rasmiy SMS so'ralmoqda...", parse_mode="HTML")

    if getattr(settings, "PHONE_AUTOMATION_ENABLED", False):
        async with async_session() as db:
            task_rec = await crud.add_vote_history(
                db=db,
                telegram_id=telegram_id,
                phone_number=clean_phone,
                project_id=project_id,
                status="PHONE_QUEUED",
                commit=True
            )
        try:
            await waiting_msg.delete()
        except Exception:
            pass
            
        await state.update_data(
            phone_number=clean_phone,
            project_id=project_id,
            task_id=task_rec.id,
            flow="phone_automation"
        )
        await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
        
        await message.answer(
            "⏳ <b>So'rov qabul qilindi!</b>\n\n"
            "Ovoz berish jarayoni navbatga qo'shildi. "
            "Operator telefoni orqali SMS so'ralishi kutilmoqda. "
            "Telefoningizga SMS kod kelishi bilan shu yerda sizga xabar beramiz 📩",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    mvc_success, mvc_msg, mvc_session = await OpenBudgetService.send_mvc_initiative_sms(
        phone_number=clean_phone,
        project_id=project_id
    )

    if mvc_success:
        await state.update_data(
            phone_number=clean_phone,
            project_id=project_id,
            session_data=mvc_session
        )
        await state.set_state(VoteStates.WAITING_FOR_SMS)
        try:
            await waiting_msg.delete()
        except Exception:
            pass

        p_clean = clean_phone[3:] if clean_phone.startswith("998") else clean_phone
        phone_display = f"+998 ({p_clean[:2]}) {p_clean[2:5]}-{p_clean[5:7]}-{p_clean[7:]}"
        await message.answer(
            f"📩 <b>Rasmiy SMS yuborildi!</b>\n\n"
            f"<code>{phone_display}</code> raqamiga yuborilgan 6 xonali SMS kodni kiriting:",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    if mvc_msg == "captcha_required":
        try:
            await waiting_msg.delete()
        except Exception:
            pass

        web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
        session_id = str(telegram_id)
        
        from utils.security import generate_session_signature
        sign = generate_session_signature(session_id, settings.BOT_TOKEN)
        
        url_with_params = f"{web_url.rstrip('/')}/captcha?session_id={session_id}&sign={sign}"
        
        await state.update_data(
            phone_number=clean_phone,
            project_id=project_id,
            flow="mvc"
        )
        await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Tasdiqlash (Men robot emasman)", web_app=WebAppInfo(url=url_with_params))],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_vote")]
        ])
        
        await message.answer(
            "🔒 <b>Xavfsizlik tekshiruvi:</b>\n\n"
            "Ovoz berishni davom ettirish uchun quyidagi tugmani bosing va <b>\"Men robot emasman\"</b> chekboxini tasdiqlang 👇",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    if mvc_msg == "already_voted":
        detail = (mvc_session or {}).get("detail") or f"Ushbu (+998{clean_phone}) raqam orqali bu mavsumda allaqachon ovoz berilgan!"
        async with async_session() as db:
            await crud.add_vote_history(
                db=db,
                telegram_id=telegram_id,
                phone_number=clean_phone,
                project_id=project_id,
                status=VoteStatus.ALREADY_VOTED
            )
        try:
            await waiting_msg.delete()
        except Exception:
            pass
        await message.answer(
            f"⚠️ <b>{html.escape(detail)}</b>\n\n"
            f"Qonun bo'yicha har bir fuqaro faqat <b>1 marta</b> ovoz bera oladi.",
            reply_markup=reply.get_user_menu(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    try:
        await waiting_msg.delete()
    except Exception:
        pass
    await message.answer(
        f"❌ <b>Xatolik yuz berdi:</b>\n{html.escape(str(mvc_msg))}\n\n"
        f"Iltimos, qaytadan urinib ko'ring yoki boshqa raqam kiriting:",
        reply_markup=reply.get_phone_keyboard(),
        parse_mode="HTML"
    )
    await state.clear()
    return

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
        flow = state_data.get("flow")
        telegram_id = message.from_user.id

        waiting_msg = await message.answer("⏳ <b>Tasdiqlash:</b> SMS kod so'ralmoqda...", parse_mode="HTML")

        if flow == "mvc":
            success, error_msg, session_data = await OpenBudgetService.send_mvc_initiative_sms(
                phone_number=phone_number,
                project_id=project_id,
                captcha_key=captcha_key
            )
        else:
            success, error_msg, session_data = await OpenBudgetService.check_and_send_sms(
                phone_number=phone_number,
                project_id=project_id,
                captcha_key=captcha_key,
                captcha_result=captcha_result
            )
        
        if not success:
            try:
                await waiting_msg.delete()
            except Exception:
                pass
            if error_msg == "captcha_required":
                web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
                session_id = str(telegram_id)
                
                from utils.security import generate_session_signature
                sign = generate_session_signature(session_id, settings.BOT_TOKEN)
                url_with_params = f"{web_url.rstrip('/')}/captcha?session_id={session_id}&sign={sign}"
                
                await state.update_data(
                    phone_number=phone_number,
                    project_id=project_id,
                    flow="mvc"
                )
                await state.set_state(VoteStates.WAITING_FOR_CAPTCHA)
                
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🤖 Tasdiqlash (Men robot emasman)", web_app=WebAppInfo(url=url_with_params))],
                    [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_vote")]
                ])
                
                await message.answer(
                    "🔒 <b>Tizim aniqladi:</b> Siz portalda ro'yxatdan o'tgansiz.\n\n"
                    "Ovoz berishni davom ettirish uchun quyidagi tugmani bosing va <b>\"Men robot emasman\"</b> chekboxini tasdiqlang 👇",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                return

            if error_msg == "server_error":
                await message.answer(
                    "⚠️ <b>Portal vaqtincha ishlamayapti.</b>\n\n"
                    "openbudget.uz serveri xatolik qaytardi. "
                    "Bir necha soniyadan so'ng qayta urinib ko'ring 🔁",
                    reply_markup=reply.get_cancel_keyboard(),
                    parse_mode="HTML"
                )
            unreg_terms = ["not_registered", "topilmadi", "foydalanuvchi", "топилмади", "фойдаланувчи", "рўйхатdan", "маълумотlari", "топилмаган", "ҳеч қандай", "mavjud emas"]
            voted_terms = ["already_voted", "allaqachon", "ovoz berilgan", "овоз берилган", "овоз берган", "бошқа рақам"]
            if error_msg == "not_registered" or any(t in error_msg.lower() for t in unreg_terms):
                await start_in_bot_registration(message, state, phone_number, project_id, message.from_user)
            elif error_msg == "already_voted" or any(t in error_msg.lower() for t in voted_terms):
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

        await state.update_data(session_data=session_data)
        await state.set_state(VoteStates.WAITING_FOR_SMS)
        
        try:
            await waiting_msg.edit_text(
                f"📩 <b>SMS yuborildi!</b>\n\n"
                f"<code>{phone_number}</code> raqamiga yuborilgan SMS tasdiqlash kodini kiriting:",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            await message.answer(
                f"📩 <b>SMS yuborildi!</b>\n\n"
                f"<code>{phone_number}</code> raqamiga yuborilgan SMS tasdiqlash kodini kiriting:",
                reply_markup=reply.get_cancel_keyboard(),
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Captcha WebApp natijasini o'qishda xato: {e}", exc_info=True)
        await message.answer("❌ Captcha ma'lumotlarini qabul qilishda xatolik yuz berdi. Qayta urinib ko'ring.")


async def execute_final_vote_casting(
    message: Message,
    state: FSMContext,
    phone_number: str,
    project_id: str,
    access_token: str,
    captcha_key: str,
    captcha_result: int,
    waiting_msg: Message = None
) -> tuple[bool, str]:
    """Ovoz berish so'rovini portalga yuboradi va mukofotlarni hisoblaydi."""
    if waiting_msg is None:
        waiting_msg = await message.answer("🔄 Ovoz berish yakunlanmoqda, kuting...")
    else:
        try:
            await waiting_msg.edit_text("🔄 Ovoz berish yakunlanmoqda, kuting...", parse_mode="HTML")
        except Exception:
            pass

    telegram_id = message.from_user.id

    try:
        success, result_msg = await OpenBudgetService.cast_vote(
            project_id=project_id,
            access_token=access_token,
            captcha_key=captcha_key,
            captcha_result=captcha_result
        )
        
        try:
            await waiting_msg.delete()
        except Exception:
            pass

        if not success:
            return False, result_msg

        async with async_session() as db:
            try:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=phone_number,
                    project_id=project_id,
                    status=VoteStatus.PENDING_VERIFY,
                    commit=True
                )

                project_settings = await crud.get_project_settings(db)
                voter_reward = project_settings.voter_reward

                clean_d = phone_number[-9:] if len(phone_number) >= 9 else phone_number
                formatted_p = f"+998 ({clean_d[:2]}) {clean_d[2:5]}-{clean_d[5:7]}-{clean_d[7:]}"
                success_text = (
                    f"🎉 <b>Tabriklaymiz! Ovoz berish qabul qilindi!</b>\n\n"
                    f"🏛 <code>{formatted_p}</code> raqamingiz orqali so'rov Open Budget portaliga yuborildi.\n"
                    f"⏳ <i>Open Budget rasmiy sahifada ovozlar ro'yxatida ko'rinishi bilan (1-3 daqiqada), balansingizga avtomatik ravishda <b>+{voter_reward:,.0f} so'm</b> qo'shiladi va sizga bu yerda tasdiq xabari keladi!</i>\n\n"
                    f"💡 <i>Holatni «💎 Mening hisobim» bo'limida kuzatishingiz mumkin.</i>"
                )
                await message.answer(success_text, reply_markup=reply.get_user_menu(), parse_mode="HTML")

                try:
                    from services.vote_verifier import verify_pending_votes_step
                    asyncio.create_task(verify_pending_votes_step(message.bot))
                except Exception:
                    pass

                await state.clear()
                return True, "pending_verify"

            except Exception as e:
                await db.rollback()
                logger.error(f"Ovoz yozish tranzaksiyasida xatolik: {e}", exc_info=True)
                await message.answer(
                    "❌ Ovoz qabul qilindi, lekin ma'lumotlarni saqlashda xatolik yuz berdi.\n"
                    "Iltimos, adminlar bilan bog'laning.",
                    reply_markup=reply.get_user_menu()
                )
                await state.clear()
                return False, "db_error"

    except Exception as e:
        logger.error(f"Ovoz yuborishda umumiy xatolik: {e}", exc_info=True)
        try:
            await waiting_msg.delete()
        except Exception:
            pass
        return False, str(e)


@router.message(VoteStates.WAITING_FOR_SMS, F.text)
async def process_sms_code(message: Message, state: FSMContext):
    """SMS kodini tekshirish va muvaffaqiyatli bo'lsa foydalanuvchilarni mukofotlash"""
    code = message.text.strip()
    
    if code == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    if not code.isdigit() or len(code) != 6:
        await message.answer(
            "❌ SMS kod <b>6 xonali raqam</b> bo'lishi kerak (Masalan: <code>159795</code>).\n"
            "Iltimos, telefoningizga kelgan 6 xonali SMS kodni kiriting:",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    if data.get("flow") == "phone_automation":
        task_id = data.get("task_id")
        async with async_session() as db:
            from database.models import VotesHistory
            task = await db.get(VotesHistory, task_id)
            if task:
                task.sms_code = code
                await db.commit()
                
        await message.answer(
            "🔄 <b>SMS kod qabul qilindi!</b>\n\n"
            "Tasdiqlash kodi operator telefoni orqali portalga yuborilmoqda. "
            "Iltimos, yakuniy natija chiqquncha biroz kuting... ⏳",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

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

    if result_msg == "mvc_voted":
        async with async_session() as db:
            try:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=phone_number,
                    project_id=project_id,
                    status=VoteStatus.PENDING_VERIFY,
                    commit=True
                )
                
                project_settings = await crud.get_project_settings(db)
                voter_reward = project_settings.voter_reward

                clean_d = phone_number[-9:] if len(phone_number) >= 9 else phone_number
                formatted_p = f"+998 ({clean_d[:2]}) {clean_d[2:5]}-{clean_d[5:7]}-{clean_d[7:]}"
                success_text = (
                    f"🎉 <b>Tabriklaymiz! Ovoz berish qabul qilindi!</b>\n\n"
                    f"🏛 <code>{formatted_p}</code> raqamingiz orqali so'rov Open Budget portaliga yuborildi.\n"
                    f"⏳ <i>Open Budget rasmiy sahifada ovozlar ro'yxatida ko'rinishi bilan (1-3 daqiqada), balansingizga avtomatik ravishda <b>+{voter_reward:,.0f} so'm</b> qo'shiladi va sizga bu yerda tasdiq xabari keladi!</i>\n\n"
                    f"💡 <i>Holatni «💎 Mening hisobim» bo'limida kuzatishingiz mumkin.</i>"
                )
                await message.answer(success_text, reply_markup=reply.get_user_menu(), parse_mode="HTML")
            except Exception as e:
                logger.error(f"MVC vote save error: {e}", exc_info=True)
                await message.answer("✅ Ovoz qabul qilindi!", reply_markup=reply.get_user_menu())

        try:
            from services.vote_verifier import verify_pending_votes_step
            asyncio.create_task(verify_pending_votes_step(message.bot))
        except Exception:
            pass

        await state.clear()
        return

    access_token = result_msg
    
    cap_waiting = await message.answer("🔄 Yakuniy xavfsizlik tekshiruvi (Captcha) yuklanmoqda...")
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    await cap_waiting.delete()

    if not success_cap or not cap_data:
        await message.answer("❌ Ovoz berish captchasini yuklab bo'lmadi. Iltimos, keyinroq qayta urinib ko'ring.")
        return

    captcha_key = cap_data.get("key")
    captcha_image = cap_data.get("image_base64")

    auto_result = None
    max_auto_attempts = 2
    for auto_attempt in range(max_auto_attempts):
        if auto_attempt > 0:
            cap_waiting = await message.answer(f"🔄 <b>Qayta urinish:</b> Yangi captcha yechilmoqda ({auto_attempt+1}/{max_auto_attempts})...")
            success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
            await cap_waiting.delete()
            if not success_cap or not cap_data:
                break
            captcha_key = cap_data.get("key")
            captcha_image = cap_data.get("image_base64")

        if not captcha_image or cap_data.get("mock"):
            break

        auto_waiting = await message.answer("🧠 <b>Yechim:</b> Captcha avtomatik yechilmoqda...", parse_mode="HTML")
        try:
            from services.captcha_solver import solve_captcha
            auto_result = await solve_captcha(captcha_image)
        except Exception as e:
            logger.warning(f"Captcha solver xatosi: {e}")
            auto_result = None
        
        await auto_waiting.delete()

        if auto_result is None:
            continue

        logger.info(f"Final Captcha avtomatik yechildi ({auto_attempt+1}-urinish): {auto_result}")
        await asyncio.sleep(1.5)
        
        final_waiting = await message.answer("🔄 Ovoz berish yakunlanmoqda, kuting...")
        success_vote, error_vote = await execute_final_vote_casting(
            message=message,
            state=state,
            phone_number=phone_number,
            project_id=project_id,
            access_token=access_token,
            captcha_key=captcha_key,
            captcha_result=auto_result,
            waiting_msg=final_waiting
        )
        
        if success_vote:
            return

        if error_vote == "already_voted":
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
            
        logger.warning(f"Avtomatik final captcha xatosi ({error_vote}), qayta uriniladi...")

    await state.update_data(
        access_token=access_token,
        captcha_key=captcha_key,
        captcha_image=captcha_image,
        phone_number=phone_number,
        project_id=project_id,
    )
    await state.set_state(VoteStates.WAITING_FOR_FINAL_CAPTCHA)

    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    session_id = str(telegram_id)
    
    await message.answer(
        "⚠️ <b>Avtomatik yechish o'xshamadi!</b>\n\n"
        "Ovoz berishni yakunlash uchun pastdagi tugmani bosing va 2-captchani o'zingiz yeching 👇",
        reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
        parse_mode="HTML"
    )


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

        success_vote, error_vote = await execute_final_vote_casting(
            message=message,
            state=state,
            phone_number=phone_number,
            project_id=project_id,
            access_token=access_token,
            captcha_key=captcha_key,
            captcha_result=captcha_result,
            waiting_msg=waiting_msg
        )

        if not success_vote:
            if error_vote == "invalid_captcha":
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
            
            elif error_vote == "already_voted":
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
                if any(x in error_vote.lower() for x in ["server", "portal", "ulanish", "timeout", "status: 5"]):
                    await message.answer(
                        f"⚠️ <b>Portalda vaqtincha xatolik yuz berdi:</b>\n{html.escape(error_vote)}\n\n"
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

                async with async_session() as db:
                    await crud.add_vote_history(
                        db=db,
                        telegram_id=telegram_id,
                        phone_number=phone_number,
                        project_id=project_id,
                        status=VoteStatus.FAILED
                    )
                await message.answer(
                    f"❌ Ovoz berish yakunlanmadi:\n<b>{html.escape(error_vote)}</b>",
                    reply_markup=reply.get_user_menu(),
                    parse_mode="HTML"
                )
                await state.clear()
                return

    except Exception as e:
        logger.error(f"Final Captcha WebApp natijasini o'qishda xato: {e}", exc_info=True)
        await message.answer("❌ Captcha ma'lumotlarini qabul qilishda xatolik yuz berdi. Qayta urinib ko'ring.")

# RO'YXATDAN O'TISH HANDLERLARI

@router.callback_query(F.data == "reg_cancel")
async def process_reg_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
    await callback.answer()

@router.callback_query(F.data == "reg_use_tg_name", VoteStates.REG_WAITING_NAME)
async def process_reg_use_tg_name(callback: CallbackQuery, state: FSMContext):
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
    await callback.message.answer(
        "✏️ Iltimos, Ism va Familiyangizni yozing:\n"
        "(Masalan: <i>Jahongir Tojiboyev</i>)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(VoteStates.REG_WAITING_NAME, F.text)
async def process_reg_custom_name_text(message: Message, state: FSMContext):
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
    text = message.text.strip()
    if text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())
        return

    clean_date = "1998-01-01"
    digits = re.findall(r'\d+', text)
    
    if len(digits) == 1 and len(digits[0]) == 4:
        year = int(digits[0])
        if 1940 <= year <= 2010:
            clean_date = f"{year}-01-01"
        else:
            clean_date = "1998-01-01"
    elif len(digits) >= 3:
        if len(digits[0]) == 4:
            clean_date = f"{digits[0]}-{digits[1].zfill(2)}-{digits[2].zfill(2)}"
        else:
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
    await state.set_state(VoteStates.REG_WAITING_REGION)
    await callback.message.answer(
        "📍 <b>Viloyatingizni tanlang:</b>",
        reply_markup=inline.get_regions_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reg_reg_"), VoteStates.REG_WAITING_REGION)
async def process_reg_region(callback: CallbackQuery, state: FSMContext):
    from utils.regions import get_region_name
    region_id = int(callback.data.replace("reg_reg_", ""))
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
    parts = callback.data.split("_")
    region_id = int(parts[2])
    district_id = int(parts[3])
    telegram_id = callback.from_user.id
    
    await state.update_data(reg_region_id=region_id, reg_district_id=district_id)

    waiting_msg = await callback.message.answer("🔄 <b>Ro'yxatdan o'tish uchun Captcha yuklanmoqda...</b>", parse_mode="HTML")
    await callback.answer()

    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()
    await waiting_msg.delete()

    if not success_cap or not cap_data:
        await callback.message.answer("❌ Captcha yuklab bo'lmadi. Qayta urinib ko'ring.")
        return

    await state.update_data(
        captcha_key=cap_data.get("key"),
        captcha_image=cap_data.get("image_base64"),
    )
    await state.set_state(VoteStates.REG_WAITING_CAPTCHA)

    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    session_id = str(telegram_id)

    await callback.message.answer(
        "🧩 <b>Ro'yxatdan o'tishni tasdiqlash (Captcha)!</b>\n\n"
        "SMS kod yuborilishi uchun pastdagi tugmani bosing va captchani yeching 👇",
        reply_markup=reply.get_captcha_reply_keyboard(session_id, web_url),
        parse_mode="HTML"
    )

@router.message(VoteStates.REG_WAITING_CAPTCHA, F.web_app_data)
async def process_reg_captcha_result(message: Message, state: FSMContext):
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
        first_name = state_data.get("reg_first_name") or "Fuqaro"
        last_name = state_data.get("reg_last_name") or ""
        birth_date = state_data.get("reg_birth_date") or "1998-01-01"
        gender = state_data.get("reg_gender") or "MALE"
        region_id = state_data.get("reg_region_id") or 1
        district_id = state_data.get("reg_district_id") or 101

        waiting_msg = await message.answer("🔄 <b>Ro'yxatdan o'tish SMS kodi so'ralmoqda...</b>", parse_mode="HTML")

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
            await message.answer(
                f"❌ <b>Ro'yxatdan o'tishda xatolik:</b>\n{html.escape(result_msg)}\n\n"
                f"Iltimos, qaytadan urinib ko'ring yoki boshqa raqam kiriting:",
                reply_markup=reply.get_phone_keyboard(),
                parse_mode="HTML"
            )
            await state.clear()
            return

        await state.update_data(session_data=session_data)
        await state.set_state(VoteStates.REG_WAITING_SMS)

        await message.answer(
            f"📩 <b>SMS yuborildi!</b>\n\n"
            f"<code>{phone_number}</code> raqamiga yuborilgan 6 xonali SMS kodni kiriting:\n\n"
            f"<i>Kodni kiritishingiz bilan hisobingiz ochiladi va ovozingiz qabul qilinadi!</i> ⚡",
            reply_markup=reply.get_cancel_keyboard(),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Reg Captcha natijasini o'qishda xato: {e}", exc_info=True)
        await message.answer("❌ Captcha ma'lumotlarini qabul qilishda xatolik yuz berdi. Qayta urinib ko'ring.")

@router.message(VoteStates.REG_WAITING_SMS, F.text)
async def process_reg_sms_code(message: Message, state: FSMContext):
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

    cap_waiting = await message.answer("✅ <b>Ro'yxatdan o'tish tasdiqlandi!</b>\n⚡ Ovozingiz rasmiylashtirilmoqda...", parse_mode="HTML")
    success_cap, cap_msg, cap_data = await OpenBudgetService.get_captcha()

    if not success_cap or not cap_data:
        try:
            await cap_waiting.delete()
        except Exception:
            pass
        await message.answer("❌ Captcha yuklab bo'lmadi. Keyinroq qayta urinib ko'ring.")
        return

    captcha_key = cap_data.get("key")
    captcha_image = cap_data.get("image_base64")

    auto_final_result = None
    if captcha_image and not cap_data.get("mock"):
        try:
            from services.captcha_solver import solve_captcha
            auto_final_result = await solve_captcha(captcha_image)
        except Exception as e:
            logger.warning(f"Final captcha solver xatosi: {e}")

    if auto_final_result is not None:
        vote_success, vote_result = await OpenBudgetService.cast_vote(
            project_id=project_id,
            access_token=access_token,
            captcha_key=captcha_key,
            captcha_result=auto_final_result
        )
        try:
            await cap_waiting.delete()
        except Exception:
            pass
        if vote_success:
            async with async_session() as db:
                await crud.add_vote_history(
                    db=db,
                    telegram_id=telegram_id,
                    phone_number=clean_phone_number(phone_number),
                    project_id=project_id,
                    status=VoteStatus.SUCCESS
                )
                settings_db = await crud.get_settings(db)
                if settings_db and settings_db.voter_reward > 0:
                    await crud.update_user_balance(db, telegram_id, settings_db.voter_reward)

            await message.answer(
                "🎉 <b>Ovozingiz muvaffaqiyatli qabul qilindi!</b>\n\n"
                "Tashabbusni qo'llab-quvvatlaganingiz uchun tashakkur! ⚡",
                reply_markup=reply.get_user_menu(),
                parse_mode="HTML"
            )
            await state.clear()
            return
        else:
            logger.warning(f"Final avtomatik ovoz berishda xatolik: {vote_result}")

    try:
        await cap_waiting.delete()
    except Exception:
        pass

    await state.update_data(
        access_token=access_token,
        captcha_key=captcha_key,
        captcha_image=captcha_image,
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
