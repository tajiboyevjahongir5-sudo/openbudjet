from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

def get_user_inline_menu() -> InlineKeyboardMarkup:
    """Foydalanuvchilar uchun zamonaviy va rangli asosiy inline menyu"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Ovoz berish", callback_data="menu_vote", style="success")
            ],
            [
                InlineKeyboardButton(text="💎 Mening hisobim", callback_data="menu_balance", style="primary"),
                InlineKeyboardButton(text="📣 Takliflar", callback_data="menu_referral", style="primary")
            ]
        ]
    )

def get_withdrawal_keyboard() -> InlineKeyboardMarkup:
    """Hisobim bo'limida pulni yechib olish uchun yashil rangli inline tugma"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pulni yechib olish", callback_data="withdraw_money", style="success")]
        ]
    )

def get_withdraw_action_keyboard(withdraw_id: int) -> InlineKeyboardMarkup:
    """Adminlar guruhiga pul yechish so'rovi borganda chiqadigan rangli (yashil/qizil) tugmalar"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{withdraw_id}", style="success"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{withdraw_id}", style="danger")
            ]
        ]
    )

def get_captcha_keyboard(session_id: str, web_url: str) -> InlineKeyboardMarkup:
    """Puzzle captchani yechish uchun yashil rangli Web App tugmasi"""
    url = f"{web_url}/captcha?session_id={session_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url), style="success")]
        ]
    )

def get_admin_projects_keyboard(projects: list[str]) -> InlineKeyboardMarkup:
    """Hisobot yuklab olish uchun loyihalar ro'yxatini ko'k rangli inline tugma shaklida chiqarish"""
    buttons = []
    for p in projects:
        buttons.append([InlineKeyboardButton(text=f"Loyiha: {p}", callback_data=f"admin_report_{p}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_inline_menu() -> InlineKeyboardMarkup:
    """Adminlar uchun boshqaruv panelining rangli inline menyusi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Loyihani o'zgartirish", callback_data="admin_change_project", style="primary"),
                InlineKeyboardButton(text="💰 Mukofot narxi", callback_data="admin_change_price", style="primary")
            ],
            [
                InlineKeyboardButton(text="💸 Min. Pul yechish", callback_data="admin_change_min", style="primary"),
                InlineKeyboardButton(text="📈 Statistika", callback_data="admin_view_stats", style="primary")
            ],
            [
                InlineKeyboardButton(text="📊 Batafsil Hisobot", callback_data="admin_select_report", style="success")
            ]
        ]
    )
