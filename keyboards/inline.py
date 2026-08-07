from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

def get_withdrawal_keyboard() -> InlineKeyboardMarkup:
    """Hisobim bo'limida pulni yechib olish uchun inline tugma"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pulni yechib olish", callback_data="withdraw_money")]
        ]
    )

def get_withdraw_action_keyboard(withdraw_id: int) -> InlineKeyboardMarkup:
    """Adminlar guruhiga pul yechish so'rovi borganda chiqadigan tasdiqlash/rad etish tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{withdraw_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{withdraw_id}")
            ]
        ]
    )

def get_captcha_keyboard(session_id: str, web_url: str) -> InlineKeyboardMarkup:
    """Puzzle captchani yechish uchun Web App tugmasi"""
    url = f"{web_url}/captcha?session_id={session_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url))]
        ]
    )
