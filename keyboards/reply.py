from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from config import settings
from utils.security import generate_session_signature

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy va rangli asosiy menyu (Reply Keyboard)"""
    keyboard = [
        [KeyboardButton(text="⚡ Ovoz berish", style="success")],
        [
            KeyboardButton(text="💎 Mening hisobim", style="primary"), 
            KeyboardButton(text="📣 Do'stlarni taklif qilish", style="primary")
        ],
        [
            KeyboardButton(text="🤝 Hamkorlik", style="primary")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        keep_placeholder=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Ovoz berishda telefon raqamini olish uchun rangli tugma"""
    keyboard = [
        [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True, style="success")],
        [KeyboardButton(text="❌ Jarayonni bekor qilish", style="danger")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Adminlar uchun boshqaruv paneli menyusi (Reply Keyboard)"""
    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    dashboard_url = f"{web_url}/admin/api-dashboard"
    
    keyboard = [
        [
            KeyboardButton(text="📂 Loyihalar", style="primary"), 
            KeyboardButton(text="💰 Ovoz mukofoti", style="primary")
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
            KeyboardButton(text="📊 Batafsil Hisobot", style="success")
        ],
        [
            KeyboardButton(text="📣 Reklama yuborish", style="success"),
            KeyboardButton(text="⚙️ Sozlamalar", style="success")
        ],
        [
            KeyboardButton(text="🔙 Asosiy menyu", style="primary")
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

