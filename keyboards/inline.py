from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from database.models import OpenBudgetProject

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
    """Hisobim bo'limida pulni yechib olish uchun inline tugma"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pulni yechib olish", callback_data="withdraw_money", style="success")]
        ]
    )

def get_withdraw_action_keyboard(withdraw_id: int) -> InlineKeyboardMarkup:
    """Adminlar guruhiga pul yechish so'rovi borganda chiqadigan tugmalar"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{withdraw_id}", style="success"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{withdraw_id}", style="danger")
            ],
            [
                InlineKeyboardButton(text="👁️ Karta raqamini ko'rish", callback_data=f"reveal_card_{withdraw_id}", style="primary")
            ]
        ]
    )

def get_captcha_keyboard(session_id: str, web_url: str) -> InlineKeyboardMarkup:
    """Puzzle captchani yechish uchun Web App tugmasi"""
    url = f"{web_url}/captcha?session_id={session_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Captchani yechish", web_app=WebAppInfo(url=url), style="success")]
        ]
    )

def get_admin_projects_keyboard(projects: list[str]) -> InlineKeyboardMarkup:
    """Hisobot yuklab olish uchun loyihalar ro'yxatini inline tugma shaklida chiqarish"""
    buttons = []
    for p in projects:
        buttons.append([InlineKeyboardButton(text=f"Loyiha: {p}", callback_data=f"admin_report_{p}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_inline_menu() -> InlineKeyboardMarkup:
    """Adminlar uchun boshqaruv panelining inline menyusi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📂 Loyihalar", callback_data="admin_proj_list", style="primary")
            ],
            [
                InlineKeyboardButton(text="💰 Ovoz mukofoti", callback_data="admin_change_voter_reward", style="primary"),
                InlineKeyboardButton(text="👥 Referal mukofoti", callback_data="admin_change_referral_price", style="primary")
            ],
            [
                InlineKeyboardButton(text="💸 Min. Pul yechish", callback_data="admin_change_min", style="primary"),
                InlineKeyboardButton(text="🔒 Maxfiy kanal", callback_data="admin_change_channel", style="primary")
            ],
            [
                InlineKeyboardButton(text="📈 Statistika", callback_data="admin_view_stats", style="primary"),
                InlineKeyboardButton(text="📊 Hisobot", callback_data="admin_select_report", style="success")
            ]
        ]
    )


def get_admin_projects_list_keyboard(projects: list[OpenBudgetProject]) -> InlineKeyboardMarkup:
    """Barcha qo'shilgan loyihalar ro'yxatini va amallarni chiqarish"""
    buttons = []
    # Loyihalarni chiqarish
    for p in projects:
        status_star = "🟢 Faol: " if p.is_active else "🔴 Faolsiz: "
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_star}{p.project_id}",
                callback_data=f"admin_proj_view_{p.project_id}",
                style="primary"
            )
        ])
    
    # Boshqaruv tugmalari
    buttons.append([
        InlineKeyboardButton(text="➕ Loyiha qo'shish", callback_data="admin_proj_add", style="success")
    ])
    
    if any(p.is_active for p in projects):
        buttons.append([
            InlineKeyboardButton(text="🔴 Barchasini faolsizlantirish", callback_data="admin_proj_deactivate_all", style="danger")
        ])
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_project_manage_keyboard(project: OpenBudgetProject) -> InlineKeyboardMarkup:
    """Bitta loyihani boshqarish tugmalari"""
    buttons = []
    
    # Faollashtirish yoki faolsizlantirish tugmasi
    if project.is_active:
        buttons.append([
            InlineKeyboardButton(text="🔴 Faolsizlantirish", callback_data=f"admin_proj_deactivate_{project.project_id}", style="danger")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🟢 Faollashtirish", callback_data=f"admin_proj_activate_{project.project_id}", style="success")
        ])
        
    # O'chirish va orqaga tugmalari
    buttons.append([
        InlineKeyboardButton(text="🗑️ Loyihani o'chirish", callback_data=f"admin_proj_delete_{project.project_id}", style="danger")
    ])
    buttons.append([
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_proj_list", style="primary")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
