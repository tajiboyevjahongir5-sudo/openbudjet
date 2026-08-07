from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_user_menu() -> ReplyKeyboardMarkup:
    """Foydalanuvchilar uchun asosiy menyu"""
    keyboard = [
        [KeyboardButton(text="🗳️ Ovoz berish")],
        [KeyboardButton(text="💰 Hisobim"), KeyboardButton(text="🔗 Referal")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        keep_placeholder=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Ovoz berishda telefon raqamini olish uchun tugma"""
    keyboard = [
        [KeyboardButton(text="📱 Kontaktni ulashish", request_contact=True)],
        [KeyboardButton(text="❌ Bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Adminlar uchun boshqaruv paneli menyusi"""
    keyboard = [
        [KeyboardButton(text="🔗 Havolani o'zgartirish"), KeyboardButton(text="💵 Referal narxi")],
        [KeyboardButton(text="💳 Min. Yechish"), KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📋 Hisobot"), KeyboardButton(text="⬅️ Asosiy menyu")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Bekor qilish (FSM jarayonidan chiqish) tugmasi"""
    keyboard = [
        [KeyboardButton(text="❌ Bekor qilish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )
