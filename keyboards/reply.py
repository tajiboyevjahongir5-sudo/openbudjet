from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy asosiy menyu"""
    keyboard = [
        [KeyboardButton(text="⚡ Ovoz berish")],
        [KeyboardButton(text="💎 Mening hisobim"), KeyboardButton(text="📣 Do'stlarni taklif qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        keep_placeholder=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Ovoz berishda telefon raqamini olish uchun tugma"""
    keyboard = [
        [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)],
        [KeyboardButton(text="❌ Jarayonni bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Adminlar uchun zamonaviy boshqaruv paneli menyusi"""
    keyboard = [
        [KeyboardButton(text="✏️ Loyihani o'zgartirish"), KeyboardButton(text="💰 Mukofot narxi")],
        [KeyboardButton(text="💸 Min. Pul yechish"), KeyboardButton(text="📈 Statistika")],
        [KeyboardButton(text="📊 Batafsil Hisobot"), KeyboardButton(text="🔙 Asosiy menyu")]
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
