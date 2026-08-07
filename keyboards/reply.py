from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy va rangli asosiy menyu (Reply Keyboard)"""
    keyboard = [
        [KeyboardButton(text="⚡ Ovoz berish", style="success")],
        [
            KeyboardButton(text="💎 Mening hisobim", style="primary"), 
            KeyboardButton(text="📣 Do'stlarni taklif qilish", style="primary")
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
    keyboard = [
        [
            KeyboardButton(text="📂 Loyihalar", style="primary"), 
            KeyboardButton(text="💰 Mukofot narxi", style="primary")
        ],
        [
            KeyboardButton(text="💸 Min. Pul yechish", style="primary"), 
            KeyboardButton(text="📈 Statistika", style="primary")
        ],
        [
            KeyboardButton(text="📊 Batafsil Hisobot", style="success"), 
            KeyboardButton(text="📣 Reklama yuborish", style="success")
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
