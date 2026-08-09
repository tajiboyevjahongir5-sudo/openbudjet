from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy asosiy menyu (Reply Keyboard)"""
    keyboard = [
        [KeyboardButton(text="⚡ Ovoz berish")],
        [
            KeyboardButton(text="💎 Mening hisobim"), 
            KeyboardButton(text="📣 Do'stlarni taklif qilish")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        keep_placeholder=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Ovoz berishda telefon raqamini olish uchun tugmalar"""
    keyboard = [
        [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Adminlar uchun boshqaruv paneli menyusi (Reply Keyboard)"""
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
            KeyboardButton(text="📊 Batafsil Hisobot"),
            KeyboardButton(text="📣 Reklama yuborish")
        ],
        [
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
    url = f"{web_url}/captcha?session_id={session_id}"
    keyboard = [
        [KeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url))],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )
