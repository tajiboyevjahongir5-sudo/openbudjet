from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from config import settings
from utils.security import generate_session_signature

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy va rangli asosiy menyu (Reply Keyboard)"""
    keyboard = [
        [KeyboardButton(text="🟢  ⚡ Ovoz berish 🗳️")],
        [
            KeyboardButton(text="🔵  💎 Mening hisobim"), 
            KeyboardButton(text="🟣  👥 Do'stlarni taklif qilish")
        ],
        [
            KeyboardButton(text="🟠  🤝 Hamkorlik & API")
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
        [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
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
            KeyboardButton(text="📂 Loyihalar"), 
            KeyboardButton(text="💰 Ovoz mukofoti")
        ],
        [
            KeyboardButton(text="👥 Referal mukofoti"), 
            KeyboardButton(text="💸 Min. Pul yechish")
        ],
        [
            KeyboardButton(text="📈 Statistika"),
            KeyboardButton(text="🔒 Maxfiy kanal")
        ],
        [
            KeyboardButton(text="🔑 API Web App", web_app=WebAppInfo(url=dashboard_url)),
            KeyboardButton(text="📊 Batafsil Hisobot")
        ],
        [
            KeyboardButton(text="📣 Reklama yuborish"),
            KeyboardButton(text="🔙 Asosiy menyu")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Bekor qilish tugmasi"""
    keyboard = [
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_captcha_reply_keyboard(session_id: str, web_url: str) -> ReplyKeyboardMarkup:
    """Captcha yechish uchun Web App (Reply Keyboard) - sendData ishlashi uchun Reply Keyboard shart!"""
    sign = generate_session_signature(session_id, settings.BOT_TOKEN)
    url = f"{web_url}/captcha?session_id={session_id}&sign={sign}"
    keyboard = [
        [KeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url))],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
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

