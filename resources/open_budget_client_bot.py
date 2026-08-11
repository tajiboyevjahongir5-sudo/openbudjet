import logging
import asyncio
import os
import aiohttp
import sqlite3
from datetime import datetime
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKEN_SHU_YERGA_YOZILADI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Bot egasining Telegram ID raqami
API_URL = os.getenv("API_URL", "https://openbudjet-production.up.railway.app/api/v1")

# --- Ma'lumotlar Bazasi (SQLite) ---
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
    # Muvaffaqiyatli ovozlar tarixi jadvali (telefon va sana)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS votes_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            voted_at TEXT NOT NULL
        )
    """)
    # Foydalanuvchilar balanslari jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            votes_count INTEGER DEFAULT 0
        )
    """)
    # Pul yechish so'rovlari jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            card_number TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT NOT NULL
        )
    """)
    # Boshlang'ich qiymatlar
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('api_key', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('project_id', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('voter_reward', '1000')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdrawal', '5000')")
    cursor.execute("INSERT OR IGNORE INTO stats (key, val_int) VALUES ('successful_votes', 0)")
    conn.commit()
    conn.close()

# --- Database Helperlari ---

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

def add_vote_to_history(phone_number: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO votes_history (phone_number, voted_at) VALUES (?, ?)", (phone_number, now_str))
    conn.commit()
    conn.close()

def get_votes_history_list() -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT phone_number, voted_at FROM votes_history ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_or_create_user(telegram_id: int) -> tuple:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, balance, votes_count FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (telegram_id, balance, votes_count) VALUES (?, 0, 0)", (telegram_id,))
        conn.commit()
        row = (telegram_id, 0, 0)
    conn.close()
    return row

def add_user_balance_and_vote(telegram_id: int, reward: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Foydalanuvchi borligini tekshiramiz
    get_or_create_user(telegram_id)
    cursor.execute("UPDATE users SET balance = balance + ?, votes_count = votes_count + 1 WHERE telegram_id = ?", (reward, telegram_id))
    conn.commit()
    conn.close()

def create_withdrawal_request(telegram_id: int, amount: int, card_number: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO withdrawals (telegram_id, amount, card_number, status, created_at) VALUES (?, ?, ?, 'PENDING', ?)",
                   (telegram_id, amount, card_number, now_str))
    # Balansdan ayirib turamiz
    cursor.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amount, telegram_id))
    conn.commit()
    last_id = cursor.lastrowid
    conn.close()
    return last_id

def get_pending_withdrawals_list() -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, telegram_id, amount, card_number, created_at FROM withdrawals WHERE status = 'PENDING' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def process_withdrawal_db(wd_id: int, approve: bool) -> tuple:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, amount FROM withdrawals WHERE id = ? AND status = 'PENDING'", (wd_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, 0, 0
        
    telegram_id, amount = row
    status = 'APPROVED' if approve else 'REJECTED'
    cursor.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, wd_id))
    
    if not approve:
        # Agar rad etilsa pulni user balansiga qaytaramiz
        cursor.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, telegram_id))
        
    conn.commit()
    conn.close()
    return True, telegram_id, amount

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
    WAITING_FOR_REWARD = State()
    WAITING_FOR_MIN_WITHDRAW = State()

class WithdrawStates(StatesGroup):
    WAITING_FOR_CARD = State()
    WAITING_FOR_AMOUNT = State()

# --- Keyboards ---
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡ Ovoz berish")],
            [KeyboardButton(text="💎 Mening hisobim")]
        ],
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
            [InlineKeyboardButton(text="💰 Ovoz mukofotini sozlash", callback_data="client_admin_set_reward")],
            [InlineKeyboardButton(text="💸 Min. Pul yechishni sozlash", callback_data="client_admin_set_min_wd")],
            [InlineKeyboardButton(text="📊 Ovozlar hisoboti (TXT)", callback_data="client_admin_view_stats")],
            [InlineKeyboardButton(text="💸 Pul yechish so'rovlari", callback_data="client_admin_wd_requests")],
            [InlineKeyboardButton(text="❌ Menyuni yopish", callback_data="client_admin_close")]
        ]
    )

# User balance keyboard
def get_balance_keyboard(show_withdraw: bool):
    buttons = []
    if show_withdraw:
        buttons.append([InlineKeyboardButton(text="💸 Pul yechib olish", callback_data="client_user_withdraw")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Admin withdrawal requests inline key
def get_admin_wd_request_keyboard(wd_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"client_admin_app_wd_{wd_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"client_admin_rej_wd_{wd_id}")
            ]
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
    voter_reward = get_setting("voter_reward")
    min_withdrawal = get_setting("min_withdrawal")
    votes_count = get_votes_count()
    
    text = (
        "⚙️ **Mijoz Boti Admin Paneli**\n\n"
        f"🔑 API Kalit: <code>{api_key or 'Kiritilmagan'}</code>\n"
        f"📌 Loyiha IDsi: <code>{project_id or 'Kiritilmagan'}</code>\n"
        f"💰 Ovoz beruvchi mukofoti: <b>{voter_reward} UZS</b>\n"
        f"💸 Min. Pul yechish: <b>{min_withdrawal} UZS</b>\n"
        f"📈 Bot orqali jami ovozlar: <b>{votes_count} ta</b>\n\n"
        "Sozlash yoki hisobotlarni olish uchun tugmalardan foydalaning:"
    )
    await message.answer(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "client_admin_close")
async def client_admin_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "client_admin_view_stats")
async def client_admin_view_stats(callback: CallbackQuery):
    history = get_votes_history_list()
    
    if not history:
        await callback.answer("Hozircha muvaffaqiyatli ovozlar tarixi mavjud emas.", show_alert=True)
        return
        
    report_lines = [
        "========================================",
        f"      OPEN BUDGET OVOZ BERISH HISOBOTI",
        "========================================",
        f"Yaratilgan sana: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Jami muvaffaqiyatli ovozlar: {len(history)} ta\n",
        "T/R | Telefon raqam | Sana va Vaqt",
        "----------------------------------------"
    ]
    
    for idx, (phone, date_str) in enumerate(history, 1):
        report_lines.append(f"{idx:03d} | +{phone} | {date_str}")
        
    report_lines.append("\n========================================")
    report_text = "\n".join(report_lines)
    
    file_bytes = report_text.encode("utf-8")
    report_file = BufferedInputFile(file_bytes, filename=f"ovozlar_hisoboti_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    
    await callback.message.answer_document(
        document=report_file,
        caption="📊 **Botingiz orqali berilgan barcha ovozlar hisoboti (TXT formatda)**"
    )
    await callback.answer()

@router.callback_query(F.data == "client_admin_wd_requests")
async def client_admin_wd_requests(callback: CallbackQuery):
    requests = get_pending_withdrawals_list()
    if not requests:
        await callback.answer("Hozircha pul yechish uchun faol so'rovlar mavjud emas.", show_alert=True)
        return
        
    await callback.message.answer("💸 **Kutilayotgan pul yechish so'rovlari:**")
    for req_id, telegram_id, amount, card, date_str in requests:
        txt = (
            f"🆔 So'rov ID: <b>{req_id}</b>\n"
            f"👤 Foydalanuvchi Telegram ID: <code>{telegram_id}</code>\n"
            f"💳 Karta raqami: <code>{card}</code>\n"
            f"💵 Summa: <b>{amount:,} UZS</b>\n"
            f"📅 Sana: {date_str}"
        )
        await callback.message.answer(txt, reply_markup=get_admin_wd_request_keyboard(req_id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("client_admin_app_wd_"))
async def process_approve_wd(callback: CallbackQuery):
    wd_id = int(callback.data.split("_")[-1])
    success, user_id, amount = process_withdrawal_db(wd_id, approve=True)
    if success:
        await callback.message.edit_text(callback.message.text + "\n\n✅ **TASDIQLANDI**")
        try:
            await bot.send_message(user_id, f"🎉 **Tabriklaymiz! Sizning {amount:,} UZS yechish so'rovingiz tasdiqlandi va kartangizga o'tkazildi.**")
        except Exception:
            pass
    await callback.answer()

@router.callback_query(F.data.startswith("client_admin_rej_wd_"))
async def process_reject_wd(callback: CallbackQuery):
    wd_id = int(callback.data.split("_")[-1])
    success, user_id, amount = process_withdrawal_db(wd_id, approve=False)
    if success:
        await callback.message.edit_text(callback.message.text + "\n\n❌ **RAD ETILDI (Pul balansga qaytarildi)**")
        try:
            await bot.send_message(user_id, f"⚠️ **Sizning {amount:,} UZS yechish so'rovingiz administrator tomonidan rad etildi va mablag' balansingizga qaytarildi.**")
        except Exception:
            pass
    await callback.answer()

@router.callback_query(F.data == "client_admin_set_api")
async def client_admin_set_api(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_API_KEY)
    await callback.message.answer("🔑 **Yangi API kalitni yuboring (Masalan: ob_api_...):**", reply_markup=get_cancel_keyboard())
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
    await callback.message.answer("📌 **Yangi Loyiha ID sini (Project ID) yuboring (Faqat raqamlar, masalan: 32541):**", reply_markup=get_cancel_keyboard())
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

@router.callback_query(F.data == "client_admin_set_reward")
async def client_admin_set_reward(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_REWARD)
    await callback.message.answer("💰 **Har bir muvaffaqiyatli ovoz uchun to'lanadigan mukofot summasini kiriting (UZS):**", reply_markup=get_cancel_keyboard())
    await callback.answer()

@router.message(AdminStates.WAITING_FOR_REWARD, F.text)
async def process_set_reward(message: Message, state: FSMContext):
    text_input = message.text.strip()
    if text_input == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not text_input.isdigit():
        await message.answer("❌ Noto'g'ri summa! Iltimos, faqat musbat raqam kiriting:")
        return
        
    set_setting("voter_reward", text_input)
    await state.clear()
    await message.answer("✅ Ovoz beruvchi mukofoti muvaffaqiyatli yangilandi!", reply_markup=get_main_menu())

@router.callback_query(F.data == "client_admin_set_min_wd")
async def client_admin_set_min_wd(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_MIN_WITHDRAW)
    await callback.message.answer("💸 **Minimal pul yechish miqdorini kiriting (UZS):**", reply_markup=get_cancel_keyboard())
    await callback.answer()

@router.message(AdminStates.WAITING_FOR_MIN_WITHDRAW, F.text)
async def process_set_min_wd(message: Message, state: FSMContext):
    text_input = message.text.strip()
    if text_input == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not text_input.isdigit():
        await message.answer("❌ Noto'g'ri miqdor! Iltimos, faqat musbat raqam kiriting:")
        return
        
    set_setting("min_withdrawal", text_input)
    await state.clear()
    await message.answer("✅ Minimal pul yechish miqdori muvaffaqiyatli yangilandi!", reply_markup=get_main_menu())


# --- User Balance & Withdrawal Handlers (Mening Hisobim va Pul Yechish) ---

@router.message(F.text == "💎 Mening hisobim")
async def cmd_my_account(message: Message, state: FSMContext):
    await state.clear()
    user_db = get_or_create_user(message.from_user.id)
    balance = user_db[1]
    votes = user_db[2]
    
    min_wd = int(get_setting("min_withdrawal") or "5000")
    show_wd = balance >= min_wd
    
    text = (
        "💎 **Sizning hisobingiz:**\n\n"
        f"💰 Balansingiz: <b>{balance:,} UZS</b>\n"
        f"🗳️ Siz bergan muvaffaqiyatli ovozlar: <b>{votes} ta</b>\n"
        f"💸 Minimal pul yechish miqdori: <b>{min_wd:,} UZS</b>"
    )
    await message.answer(text, reply_markup=get_balance_keyboard(show_wd), parse_mode="HTML")

@router.callback_query(F.data == "client_user_withdraw")
async def start_withdraw_process(callback: CallbackQuery, state: FSMContext):
    user_db = get_or_create_user(callback.from_user.id)
    balance = user_db[1]
    min_wd = int(get_setting("min_withdrawal") or "5000")
    
    if balance < min_wd:
        await callback.answer(f"Balansda yetarli mablag' yo'q (Min. {min_wd} UZS).", show_alert=True)
        return
        
    await state.set_state(WithdrawStates.WAITING_FOR_CARD)
    await callback.message.answer(
        "💳 **Plastik karta raqamingizni kiriting:**\n(Uzcard yoki Humo, 16 xonali raqam)",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@router.message(WithdrawStates.WAITING_FOR_CARD, F.text)
async def process_withdraw_card(message: Message, state: FSMContext):
    card = message.text.strip().replace(" ", "")
    if card == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Pul yechish bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not card.isdigit() or len(card) < 16 or len(card) > 20:
        await message.answer("❌ Karta raqami noto'g'ri! Iltimos, faqat 16-20 xonali plastik karta raqamingizni yuboring:")
        return
        
    await state.update_data(card_number=card)
    await state.set_state(WithdrawStates.WAITING_FOR_AMOUNT)
    
    user_db = get_or_create_user(message.from_user.id)
    balance = user_db[1]
    
    await message.answer(
        f"💵 **Qancha pul yechib olmoqchisiz?**\n"
        f"Maksimal summa: **{balance:,} UZS**\n\n"
        f"Iltimos, summani kiriting (Faqat raqamlarda):",
        reply_markup=get_cancel_keyboard()
    )

@router.message(WithdrawStates.WAITING_FOR_AMOUNT, F.text)
async def process_withdraw_amount(message: Message, state: FSMContext):
    amount_str = message.text.strip()
    if amount_str == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Pul yechish bekor qilindi.", reply_markup=get_main_menu())
        return
        
    if not amount_str.isdigit():
        await message.answer("❌ Noto'g'ri qiymat! Summani faqat raqamlarda yozing:")
        return
        
    amount = int(amount_str)
    user_db = get_or_create_user(message.from_user.id)
    balance = user_db[1]
    min_wd = int(get_setting("min_withdrawal") or "5000")
    
    if amount < min_wd:
        await message.answer(f"❌ Minimal pul yechish miqdori: {min_wd:,} UZS. Qayta kiriting:")
        return
        
    if amount > balance:
        await message.answer(f"❌ Balansingizda bunday summa yo'q! Maksimal yechish: {balance:,} UZS. Qayta kiriting:")
        return
        
    state_data = await state.get_data()
    card = state_data["card_number"]
    
    # Pul yechish so'rovini yaratamiz
    req_id = create_withdrawal_request(message.from_user.id, amount, card)
    await state.clear()
    
    await message.answer(
        "✅ **Pul yechish so'rovi qabul qilindi!**\n\n"
        f"🆔 So'rov ID: <b>{req_id}</b>\n"
        f"💳 Karta raqami: <code>{card}</code>\n"
        f"💵 Summa: <b>{amount:,} UZS</b>\n\n"
        f"Administrator to'lovingizni tekshirib, 24 soat ichida kartangizga o'tkazib beradi. "
        f"Mablag' hozircha hisobingizdan yechildi.",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    
    # Bot egasiga (admin) bildirishnoma yuboramiz
    if ADMIN_ID != 0:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔔 **Yangi pul yechish so'rovi keldi (ID: {req_id})!**\n"
                f"Foydalanuvchi ID: `{message.from_user.id}`\n"
                f"Karta: `{card}`\n"
                f"Summa: {amount:,} UZS\n\n"
                f"Buni tasdiqlash yoki rad etish uchun /admin paneliga kiring."
            )
        except Exception:
            pass


# --- Ovoz berish Handlers ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    api_key = get_setting("api_key")
    project_id = get_setting("project_id")
    
    # API Kalit va Loyiha ID tekshiruvi
    if not api_key or not project_id:
        await message.answer(
            "⚠️ **DIQQAT:** Tashqi sozlamalar aniqlanmadi.\n\n"
            "Bot egasi /admin buyrug'i orqali bot sozlamalariga **API Kalit** va **Loyiha ID**sini kiritishi shart!"
        )
        return

    await message.answer(
        "👋 **Open Budget Ovoz berish botiga xush kelibsiz!**\n\n"
        "Loyiha uchun ovoz berishni boshlash uchun quyidagi tugmalardan birini bosing:",
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
    phone = data["phone"]
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
        # Ovoz berish muvaffaqiyatli bo'lsa, statistika, foydalanuvchi balansi va tarix yoziladi
        reward = int(get_setting("voter_reward") or "1000")
        increment_votes_count()
        add_vote_to_history(phone)
        add_user_balance_and_vote(callback_user_id := message.from_user.id, reward)
        
        await message.answer(
            f"🎉 **Tabriklaymiz! Ovoz muvaffaqiyatli hisoblandi.**\n"
            f"Hisobingizga **+{reward:,} UZS** mukofot qo'shildi.",
            reply_markup=get_main_menu()
        )
        
    await state.clear()

async def main():
    dp.include_router(router)
    logger.info("Mijoz bot ishga tushdi.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
