import logging
import asyncio
import os
import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    ReplyKeyboardRemove, BufferedInputFile
)
import base64

# Logger sozlamalari
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Sozlamalar (Atrof-muhit o'zgaruvchilari yoki qo'lda kiriting) ---
# Dasturni ishga tushirishdan oldin .env fayl yaratib sozlang
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKEN_SHU_YERGA_YOZILADI")
API_KEY = os.getenv("API_KEY", "") # Bizdan sotib olingan API Kalit (ob_api_...)
API_URL = os.getenv("API_URL", "https://openbudjet-production.up.railway.app/api/v1")
PROJECT_ID = os.getenv("PROJECT_ID", "") # Siz ovoz to'playotgan loyiha ID (masalan: 32541)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- FSM (Holatlar) ---
class VoteStates(StatesGroup):
    WAITING_FOR_PHONE = State()
    WAITING_FOR_CAPTCHA_1 = State()
    WAITING_FOR_SMS = State()
    WAITING_FOR_CAPTCHA_2 = State()

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

# --- API Ulanish Helperlari ---
async def call_api(endpoint: str, method: str = "POST", json_data: dict = None) -> dict:
    """Bizning API Wrapperga so'rov yuborish funksiyasi"""
    headers = {
        "X-API-Key": API_KEY,
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

# --- Bot Handlers ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # API Kalit va Loyiha ID tekshiruvi
    if not API_KEY or not PROJECT_ID:
        await message.answer(
            "⚠️ **DIQQAT:** Ushbu bot to'liq sozlanganicha yo'q.\n\n"
            "Dastur ishga tushishi uchun administrator `.env` faylida **API_KEY** va **PROJECT_ID** sozlamalarini kiritishi shart!"
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
    
    await message.answer("🔄 SMS kod yuborilmoqda, kuting...")
    
    # 2. SMS yuborish so'rovi
    payload = {
        "phone_number": phone,
        "captcha_key": captcha_key,
        "captcha_result": int(captcha_result),
        "project_id": PROJECT_ID
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
    
    await message.answer("⚡ Ovoz berilmoqda, kuting...")
    
    # 5. Yakuniy Ovoz berish
    payload = {
        "project_id": PROJECT_ID,
        "access_token": access_token,
        "captcha_key": captcha_key_2,
        "captcha_result": int(captcha_result)
    }
    
    res, status = await call_api("/cast-vote", "POST", payload)
    if status != 200:
        error_msg = res.get("detail", "Ovoz berish muvaffaqiyatsiz yakunlandi.")
        await message.answer(f"❌ Xatolik: {error_msg}", reply_markup=get_main_menu())
    else:
        await message.answer("🎉 **Tabriklaymiz! Ovoz muvaffaqiyatli hisoblandi.**", reply_markup=get_main_menu())
        
    await state.clear()

async def main():
    dp.include_router(router)
    logger.info("Mijoz bot ishga tushdi.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
