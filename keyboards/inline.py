from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import settings
from utils.security import generate_session_signature
from database.models import OpenBudgetProject, Tariff

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
    sign = generate_session_signature(session_id, settings.BOT_TOKEN)
    url = f"{web_url}/captcha?session_id={session_id}&sign={sign}"
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

def get_admin_inline_menu(telegram_id: int = None) -> InlineKeyboardMarkup:
    """Adminlar uchun boshqaruv panelining inline menyusi"""
    web_url = settings.WEB_APP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    if telegram_id:
        from utils.api_auth import generate_admin_token
        token = generate_admin_token(telegram_id)
        dashboard_url = f"{web_url.rstrip('/')}/admin/api-dashboard?admin_token={token}"
    else:
        dashboard_url = f"{web_url.rstrip('/')}/admin/api-dashboard"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📂 Loyihalar", callback_data="admin_proj_list", style="primary")
            ],
            [
                InlineKeyboardButton(text="🔑 API Web App", web_app=WebAppInfo(url=dashboard_url), style="success")
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



from urllib.parse import urlparse, parse_qs

def _get_display_id(project_id: str, url: str) -> str:
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        val = q.get("initiativeId", [None])[0]
        if val:
            return val
    except Exception:
        pass
    return project_id

def get_admin_projects_list_keyboard(projects: list[OpenBudgetProject]) -> InlineKeyboardMarkup:
    """Barcha qo'shilgan loyihalar ro'yxatini va amallarni chiqarish"""
    buttons = []
    # Loyihalarni chiqarish
    for p in projects:
        status_star = "🟢 Faol: " if p.is_active else "🔴 Faolsiz: "
        display_id = _get_display_id(p.project_id, p.project_url)
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_star}{display_id}",
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

def get_start_warning_keyboard() -> InlineKeyboardMarkup:
    """Start buyrug'i yuborilganda ro'yxatdan o'tish ogohlantirishi uchun inline tugmalar"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔗 Ro'yxatdan o'tish", url="https://openbudget.uz/registeration"),
                InlineKeyboardButton(text="✅ Ro'yxatdan o'tganman", callback_data="user_registered_confirm")
            ]
        ]
    )


# --- Hamkorlik va API sotib olish inline tugmalari ---

def get_partnership_keyboard() -> InlineKeyboardMarkup:
    """Hamkorlik menyusi inline tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💻 Bot kodi (Tayyor)", callback_data="partnership_get_code", style="primary")
            ],
            [
                InlineKeyboardButton(text="🔑 API Kalit sotib olish", callback_data="partnership_buy_api", style="success")
            ]
        ]
    )

def get_tariffs_keyboard(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    """API kalit sotib olish uchun tariflar ro'yxati (dinamik)"""
    buttons = []
    for tariff in tariffs:
        buttons.append([
            InlineKeyboardButton(
                text=f"💎 {tariff.name} ({tariff.price:,} so'm)",
                callback_data=f"buy_tariff_{tariff.votes}",
                style="primary"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="partnership_back", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_keyboard(purchase_id: int) -> InlineKeyboardMarkup:
    """To'lov tasdiqlash uchun inline tugmalar"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'ladim", callback_data=f"payment_paid_{purchase_id}", style="success"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"payment_cancel_{purchase_id}", style="danger")
            ]
        ]
    )

def get_admin_settings_keyboard() -> InlineKeyboardMarkup:
    """Admin sozlamalari inline menyusi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Karta raqamini sozlash", callback_data="admin_set_card")],
            [InlineKeyboardButton(text="📣 To'lov kanalini sozlash", callback_data="admin_set_payment_channel")],
            [InlineKeyboardButton(text="🔙 Chiqish", callback_data="admin_settings_close")]
        ]
    )

def get_admin_tariffs_keyboard(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    """Admin uchun tarifni tanlab narxini o'zgartirish inline klaviaturasi"""
    buttons = []
    for tariff in tariffs:
        buttons.append([
            InlineKeyboardButton(
                text=f"✏️ {tariff.name} ({tariff.price:,} UZS)",
                callback_data=f"admin_edit_tariff_{tariff.votes}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_settings_back", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)




