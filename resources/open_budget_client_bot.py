import logging
import asyncio
import os
import aiohttp
import sqlite3
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    ReplyKeyboardRemove, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
import base64

# Logger sozlamalari
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Boshlang'ich Sozlamalar ---
# Faqat BOT_TOKEN va ADMIN_ID ni .env orqali yoki bu yerda kiritsangiz kifoya.
# Qolgan sozlamalar (API_KEY, PROJECT_ID) bot ichidagi /admin panel orqali sozlanadi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKEN_SHU_YERGA_YOZILADI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Bot egasining Telegram ID raqami (masalan: 12345678)
API_URL = os.getenv("API_URL", "https://openbudjet-production.up.railway.app/api/v1")

# --- Ma'lumotlar Bazasi (SQLite - Kutubxona talab qilmaydi, standart o'rnatilgan) ---
DB_PATH = "client_bot.db"

def init_local_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Sozlamalar jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Muvaffaqiyatli ovozlar sonini saqlash jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            val_int INTEGER
        )
    """)
    # Boshlang'ich qiymatlar
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('api_key', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('project_id', '')")
    cursor.execute("INSERT OR IGNORE INTO stats (key, val_int) VALUES ('successful_votes', 0)")
    conn.commit()
    conn.close()

def get_setting(key: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""

def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    conn.commit()
    conn.close()

def get_votes_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT val_int FROM stats WHERE key = 'successful_votes'")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_votes_count():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE stats SET val_int = val_int + 1 WHERE key = 'successful_votes'")
    conn.commit()
    conn.close()

# Baza yaratamiz
init_local_db()

# Botni yaratish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- FSM (Holatlar) ---
class VoteStates(StatesGroup):
    WAITING_FOR_PHONE = State()
    WAITING_FOR_CAPTCHA_1 = State()
    WAITING_FOR_SMS = State()
    WAITING_FOR_CAPTCHA_2 = State()

class AdminStates(StatesGroup):
    WAITING_FOR_API_KEY = State()
    WAITING_FOR_PROJECT_ID = State()

# --- Keyboards ---
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⚡ Ovoz berish")]],
        resize_keyboard=True
    )

def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

# Admin inline keyboard
def get_admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 API Kalitni sozlash", callback_data="client_admin_set_api")],
            [InlineKeyboardButton(text="📌 Loyiha ID sini sozlash", callback_data="client_admin_set_project")],
            [InlineKeyboardButton(text="📈 Statistika", callback_data="client_admin_view_stats")],
            [InlineKeyboardButton(text="❌ Menyuni yopish", callback_data="client_admin_close")]
        ]
    )

# --- API Ulanish Helperlari ---
async def call_api(endpoint: str, method: str = "POST", json_data: dict = None) -> dict:
    """Bizning API Wrapperga so'rov yuborish funksiyasi"""
    api_key = get_setting("api_key")
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"{API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    
    async with aiohttp.ClientSession() as session:
        try:
            if method.upper() == "GET":
                async with session.get(url, headers=headers) as resp:
                    return await resp.json(), resp.status
            else:
                async with session.post(url, headers=headers, json=json_data) as resp:
                    return await resp.json(), resp.status
        except Exception as e:
            logger.error(f"API ulanish xatosi: {e}")
            return {"detail": "API server bilan ulanib bo'lmadi."}, 502

# --- Admin Handlers (Mijoz Admin paneli) ---

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    
    # Faqat ADMIN_ID ga mos keladigan foydalanuvchi kira oladi
    if ADMIN_ID == 0 or message.from_user.id != ADMIN_ID:
        return
        
    api_key = get_setting("api_key")
    project_id = get_setting("project_id")
    votes_count = get_votes_count()
    
    text = (
        "⚙️ **Mijoz Boti Admin Paneli**\n\n"
        f"🔑 API Kalit: <code>{api_key or 'Kiritilmagan'}</code>\n"
        f"📌 Loyiha IDsi: <code>{project_id or 'Kiritilmagan'}</code>\n"
        f"📈 Ovozlar soni: <b>{votes_count} ta</b>\n\n"
        "O'zgartirish uchun quyidagi tugmalardan foydalaning:"
    )
    await message.answer(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "client_admin_close")
async def client_admin_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "client_admin_view_stats")
async def client_admin_view_stats(callback: CallbackQuery):
    votes_count = get_votes_count()
    await callback.answer(f"Bot orqali jami {votes_count} ta muvaffaqiyatli ovoz berilgan.", show_alert=True)

@router.callback_query(F.data == "client_admin_set_api")
async def client_admin_set_api(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_API_KEY)
    await callback.message.answer(
        "🔑 **Yangi API kalitni yuboring (Masalan: ob_api_...):**",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@router.message(AdminStates.WAITING_FOR_API_KEY, F.text)
async def process_set_api_key(message: Message, state: FSMContext):
    text_input = message.text.strip()
    if text_input == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not text_input.startswith("ob_api_"):
        await message.answer("❌ Noto'g'ri API kalit! Kalit 'ob_api_' bilan boshlanishi shart. Qayta urinib ko'ring:")
        return
        
    set_setting("api_key", text_input)
    await state.clear()
    await message.answer("✅ API kalit muvaffaqiyatli saqlandi!", reply_markup=get_main_menu())

@router.callback_query(F.data == "client_admin_set_project")
async def client_admin_set_project(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_PROJECT_ID)
    await callback.message.answer(
        "📌 **Yangi Loyiha ID sini (Project ID) yuboring (Faqat raqamlar, masalan: 32541):**",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@router.message(AdminStates.WAITING_FOR_PROJECT_ID, F.text)
async def process_set_project_id(message: Message, state: FSMContext):
    text_input = message.text.strip()
    if text_input == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not text_input.isdigit():
        await message.answer("❌ Noto'g'ri ID! Loyiha ID faqat raqamlardan iborat bo'lishi shart. Qayta urinib ko'ring:")
        return
        
    set_setting("project_id", text_input)
    await state.clear()
    await message.answer("✅ Loyiha IDsi muvaffaqiyatli saqlandi!", reply_markup=get_main_menu())


# --- Ovoz berish Handlers ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    api_key = get_setting("api_key")
    project_id = get_setting("project_id")
    
    # API Kalit va Loyiha ID tekshiruvi
    if not api_key or not project_id:
        await message.answer(
            "⚠️ **DIQQAT:** Ushbu bot hali to'liq sozlanmagan.\n\n"
            "Bot egasi /admin buyrug'i orqali bot sozlamalariga **API Kalit** va **Loyiha ID**sini kiritishi shart!"
        )
        return

    await message.answer(
        "👋 **Open Budget Ovoz berish botiga xush kelibsiz!**\n\n"
        "Loyiha uchun ovoz berishni boshlash uchun quyidagi tugmani bosing:",
        reply_markup=get_main_menu()
    )

@router.message(F.text == "❌ Bekor qilish")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu())

@router.message(F.text == "⚡ Ovoz berish")
async def start_vote_process(message: Message, state: FSMContext):
    await state.clear()
    
    api_key = get_setting("api_key")
    project_id = get_setting("project_id")
    
    if not api_key or not project_id:
        await message.answer("⚠️ Bot sozlanmagan. Iltimos, administratorga xabar bering.")
        return
        
    await message.answer(
        "📱 Ovoz berish uchun telefon raqamingizni quyidagi tugma orqali ulashing:",
        reply_markup=get_phone_keyboard()
    )
    await state.set_state(VoteStates.WAITING_FOR_PHONE)

@router.message(VoteStates.WAITING_FOR_PHONE)
async def process_phone(message: Message, state: FSMContext):
    phone = ""
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()
        
    # Formatlash
    phone = phone.replace("+", "").replace(" ", "")
    if not phone.startswith("998") or len(phone) != 12:
        await message.answer("Iltimos, telefon raqamni to'g'ri kiriting (Masalan: +998901234567):")
        return
        
    await state.update_data(phone=phone)
    
    # 1. Captcha yuklaymiz
    await message.answer("🔄 Captcha rasmi yuklanmoqda, kuting...", reply_markup=ReplyKeyboardRemove())
    res, status = await call_api("/captcha", "POST")
    
    if status != 200 or "captcha" not in res:
        await message.answer("❌ Captcha yuklashda xatolik yuz berdi. Iltimos, keyinroq qayta urining.", reply_markup=get_main_menu())
        await state.clear()
        return
        
    captcha_data = res["captcha"]
    captcha_key = captcha_data["key"]
    image_base64 = captcha_data["image"].split(",")[-1]
    
    # Rasm yuborish
    image_bytes = base64.b64decode(image_base64)
    photo_file = BufferedInputFile(image_bytes, filename="captcha.png")
    
    await state.update_data(captcha_key=captcha_key)
    
    await message.answer_photo(
        photo=photo_file,
        caption="🧩 **Rasmdagi raqamlarni kiriting:**",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(VoteStates.WAITING_FOR_CAPTCHA_1)

@router.message(VoteStates.WAITING_FOR_CAPTCHA_1, F.text)
async def process_captcha_1(message: Message, state: FSMContext):
    captcha_result = message.text.strip()
    if not captcha_result.isdigit():
        await message.answer("Iltimos, faqat rasmdagi raqamlarni kiriting:")
        return
        
    data = await state.get_data()
    phone = data["phone"]
    captcha_key = data["captcha_key"]
    project_id = get_setting("project_id")
    
    await message.answer("🔄 SMS kod yuborilmoqda, kuting...")
    
    # 2. SMS yuborish so'rovi
    payload = {
        "phone_number": phone,
        "captcha_key": captcha_key,
        "captcha_result": int(captcha_result),
        "project_id": project_id
    }
    
    res, status = await call_api("/send-otp", "POST", payload)
    if status != 200:
        error_msg = res.get("detail", "Xatolik yuz berdi.")
        await message.answer(f"❌ Xatolik: {error_msg}\n\nQayta urinib ko'ring:", reply_markup=get_main_menu())
        await state.clear()
        return
        
    otp_key = res.get("otp_key")
    await state.update_data(otp_key=otp_key)
    
    await message.answer(
        "💬 Telefoningizga 6 xonali SMS kod yuborildi. Uni kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(VoteStates.WAITING_FOR_SMS)

@router.message(VoteStates.WAITING_FOR_SMS, F.text)
async def process_sms(message: Message, state: FSMContext):
    sms_code = message.text.strip()
    if len(sms_code) != 6 or not sms_code.isdigit():
        await message.answer("Iltimos, 6 xonali SMS kodni kiriting:")
        return
        
    data = await state.get_data()
    phone = data["phone"]
    otp_key = data["otp_key"]
    
    await message.answer("🔄 SMS kod tekshirilmoqda, kuting...")
    
    # 3. SMS tasdiqlash
    payload = {
        "phone_number": phone,
        "otp_code": sms_code,
        "otp_key": otp_key
    }
    
    res, status = await call_api("/verify-otp", "POST", payload)
    if status != 200:
        error_msg = res.get("detail", "SMS kod xato kiritildi.")
        await message.answer(f"❌ Xatolik: {error_msg}\n\nQayta urinish uchun pastdagi tugmani bosing:", reply_markup=get_main_menu())
        await state.clear()
        return
        
    access_token = res.get("access_token")
    await state.update_data(access_token=access_token)
    
    # 4. Yakuniy ovoz uchun ikkinchi captcha yuklaymiz
    await message.answer("🔄 Ovozni rasmiylashtirish uchun 2-captcha yuklanmoqda...")
    
    res_cap, cap_status = await call_api("/captcha", "POST")
    if cap_status != 200 or "captcha" not in res_cap:
        await message.answer("❌ Captcha yuklashda xato. Ovoz berib bo'lmadi.", reply_markup=get_main_menu())
        await state.clear()
        return
        
    captcha_data = res_cap["captcha"]
    captcha_key_2 = captcha_data["key"]
    image_base64 = captcha_data["image"].split(",")[-1]
    
    image_bytes = base64.b64decode(image_base64)
    photo_file = BufferedInputFile(image_bytes, filename="captcha.png")
    
    await state.update_data(captcha_key_2=captcha_key_2)
    
    await message.answer_photo(
        photo=photo_file,
        caption="🧩 **Ovozni tasdiqlash uchun rasmdagi yangi raqamlarni kiriting:**",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(VoteStates.WAITING_FOR_CAPTCHA_2)

@router.message(VoteStates.WAITING_FOR_CAPTCHA_2, F.text)
async def process_captcha_2(message: Message, state: FSMContext):
    captcha_result = message.text.strip()
    if not captcha_result.isdigit():
        await message.answer("Iltimos, faqat rasmdagi raqamlarni kiriting:")
        return
        
    data = await state.get_data()
    access_token = data["access_token"]
    captcha_key_2 = data["captcha_key_2"]
    project_id = get_setting("project_id")
    
    await message.answer("⚡ Ovoz berilmoqda, kuting...")
    
    # 5. Yakuniy Ovoz berish
    payload = {
        "project_id": project_id,
        "access_token": access_token,
        "captcha_key": captcha_key_2,
        "captcha_result": int(captcha_result)
    }
    
    res, status = await call_api("/cast-vote", "POST", payload)
    if status != 200:
        error_msg = res.get("detail", "Ovoz berish muvaffaqiyatsiz yakunlandi.")
        await message.answer(f"❌ Xatolik: {error_msg}", reply_markup=get_main_menu())
    else:
        # Ovoz berish muvaffaqiyatli bo'lsa, statistika oshiriladi
        increment_votes_count()
        await message.answer("🎉 **Tabriklaymiz! Ovoz muvaffaqiyatli hisoblandi.**", reply_markup=get_main_menu())
        
    await state.clear()

async def main():
    dp.include_router(router)
    logger.info("Mijoz bot ishga tushdi.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
