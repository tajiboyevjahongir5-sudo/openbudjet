from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from config import settings
from utils.security import generate_session_signature

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy va rangli asosiy menyu (Reply Keyboard)"""
    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    if not web_url.startswith("http"):
        web_url = f"https://{web_url}"
    payouts_redirect_url = f"{web_url}/redirect-channel"

    keyboard = [
        [KeyboardButton(text="⚡ Ovoz berish 🗳️", style="success")],
        [
            KeyboardButton(text="💎 Mening hisobim", style="primary"), 
            KeyboardButton(text="👥 Do'stlarni taklif qilish", style="primary")
        ],
        [
            KeyboardButton(text="🤝 Hamkorlik & API", style="primary"),
            KeyboardButton(text="📢 To'lovlar kanali", web_app=WebAppInfo(url=payouts_redirect_url), style="primary")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Ovoz berishda telefon raqamini olish uchun rangli tugma"""
    keyboard = [
        [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True, style="success")],
        [KeyboardButton(text="❌ Jarayonni bekor qilish", style="danger")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )

def get_admin_menu(telegram_id: int = None) -> ReplyKeyboardMarkup:
    """Adminlar uchun boshqaruv paneli menyusi (Reply Keyboard)"""
    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    if telegram_id:
        from utils.api_auth import generate_admin_token
        token = generate_admin_token(telegram_id)
        dashboard_url = f"{web_url.rstrip('/')}/admin/api-dashboard?admin_token={token}"
    else:
        dashboard_url = f"{web_url.rstrip('/')}/admin/api-dashboard"
    
    keyboard = [
        [
            KeyboardButton(text="📂 Loyihalar", style="primary"), 
            KeyboardButton(text="💰 Ovoz mukofoti", style="success")
        ],
        [
            KeyboardButton(text="👥 Referal mukofoti", style="primary"), 
            KeyboardButton(text="💸 Min. Pul yechish", style="primary")
        ],
        [
            KeyboardButton(text="📈 Statistika", style="primary"),
            KeyboardButton(text="🔒 Maxfiy kanal", style="primary")
        ],
        [
            KeyboardButton(text="🔑 API Web App", web_app=WebAppInfo(url=dashboard_url), style="success"),
            KeyboardButton(text="📊 Batafsil Hisobot", style="primary")
        ],
        [
            KeyboardButton(text="📣 Reklama yuborish", style="primary")
        ],
        [
            KeyboardButton(text="🔙 Asosiy menyu", style="danger")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Bekor qilish tugmasi"""
    keyboard = [
        [KeyboardButton(text="❌ Jarayonni bekor qilish", style="danger")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )

def get_captcha_reply_keyboard(session_id: str, web_url: str) -> ReplyKeyboardMarkup:
    """Captcha yechish uchun Web App (Reply Keyboard) - sendData ishlashi uchun Reply Keyboard shart!"""
    sign = generate_session_signature(session_id, settings.BOT_TOKEN)
    url = f"{web_url}/captcha?session_id={session_id}&sign={sign}"
    keyboard = [
        [KeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url), style="success")],
        [KeyboardButton(text="❌ Jarayonni bekor qilish", style="danger")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_check_registration_keyboard() -> ReplyKeyboardMarkup:
    """Ro'yxatdan o'tganlikni tekshirish tugmasi"""
    keyboard = [
        [KeyboardButton(text="🔄 Ro'yxatdan o'tdim, tekshirish")],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

