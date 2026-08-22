import io
import csv
import logging
import html
from urllib.parse import urlparse
from aiogram import Router, F
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, TelegramObject, BufferedInputFile

from config import settings
from database.session import async_session
from database import crud
from database.models import WithdrawalStatus, OpenBudgetProject
from sqlalchemy import select
from states.user_states import AdminStates
from keyboards import reply, inline
from services.openbudget import OpenBudgetService

logger = logging.getLogger(__name__)
router = Router()

# Custom Filter: Faqat adminlar uchun
class IsAdmin(BaseFilter):
    async def __call__(self, obj: TelegramObject) -> bool:
        if not obj.from_user:
            return False
        return obj.from_user.id in settings.ADMIN_IDS

# Routerni admin filteri bilan himoyalash
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("clear_votes"))
async def cmd_clear_votes(message: Message):
    """Barcha ovozlar tarixini bazadan tozalash"""
    from sqlalchemy import text
    async with async_session() as db:
        await db.execute(text("DELETE FROM votes_history;"))
        await db.commit()
    await message.answer("✅ <b>Barcha ovozlar tarixi (votes_history) bazadan tozalandi!</b>\nEndi raqamlaringizni qayta sinab ko'rishingiz mumkin.", parse_mode="HTML")


@router.message(Command("admin"))
@router.message(F.text == "🔙 Asosiy menyu")
async def cmd_admin(message: Message, state: FSMContext):
    """Admin panelini ochish yoki foydalanuvchi rejimiga qaytish"""
    await state.clear()
    
    if "Asosiy menyu" in message.text:
        await message.answer("Asosiy menyuga qaytdingiz.", reply_markup=reply.get_user_menu())
        return

    await message.answer(
        "🛠️ <b>Admin boshqaruv paneliga xush kelibsiz!</b>\n\n"
        "Quyidagi tugmalar orqali bot sozlamalarini boshqarishingiz mumkin:",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="HTML"
    )

# --- Helper to show projects list ---

async def show_projects_list(message_or_callback, state: FSMContext):
    await state.clear()
    async with async_session() as db:
        projects = await crud.get_all_projects(db)
    
    text = (
        "📂 <b>Open Budget loyihalari ro'yxati</b>\n\n"
        "Quyidagi loyihalardan birini tanlab faollashtirishingiz, faolsizlantirishingiz yoki o'chirishingiz mumkin. "
        "Yoki yangi loyiha qo'shishingiz mumkin:\n\n"
        "<i>(Eslatma: Bir vaqtda faqat 1 ta loyiha faol bo'la oladi)</i>"
    )
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=inline.get_admin_projects_list_keyboard(projects), parse_mode="HTML")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=inline.get_admin_projects_list_keyboard(projects), parse_mode="HTML")

# --- 1. Loyihalar boshqaruvi handlers ---

@router.message(F.text.contains("Loyihalar"))
async def admin_projects_list_message(message: Message, state: FSMContext):
    """Admin '📂 Loyihalar' tugmasini bosganda loyihalar ro'yxatini chiqaradi"""
    await show_projects_list(message, state)

@router.callback_query(F.data == "admin_proj_add")
async def process_admin_project_add_callback(callback: CallbackQuery, state: FSMContext):
    """Loyihalar ro'yxatidagi '➕ Loyiha qo'shish' tugmasini bosganda ishlaydi"""
    await state.clear()
    await state.set_state(AdminStates.WAITING_FOR_PROJECT_ID)
    await callback.message.answer(
        "📌 Yangi loyiha qo'shish uchun <b>Loyiha ID</b> raqamini kiriting:\n"
        "(Faqat raqamlardan iborat bo'lishi kerak, masalan: 32541)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AdminStates.WAITING_FOR_PROJECT_ID, F.text)
async def process_admin_project_id(message: Message, state: FSMContext):
    project_id = message.text.strip()
    if project_id == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    # Loyiha ID faqat raqamdan iborat bo'lishi shart
    if not project_id.isdigit():
        await message.answer(
            "❌ Noto'g'ri ID. Loyiha ID raqami faqat raqamlardan iborat bo'lishi kerak.\n"
            "Iltimos, qayta kiriting (masalan: 538436):"
        )
        return

    waiting_msg = await message.answer("🔍 Loyiha saytdan tekshirilmoqda, iltimos kuting...")

    try:
        # Loyihani Open Budget API orqali qidiramiz
        initiative = await OpenBudgetService.find_initiative(project_id)
    except Exception as e:
        logger.error(f"Loyiha qidirishda xatolik: {e}")
        initiative = None

    await waiting_msg.delete()

    if not initiative:
        await message.answer(
            "❌ Ushbu ID raqami bo'yicha loyiha topilmadi.\n"
            "Iltimos, ID to'g'ri kiritilganligini tekshirib, qayta kiriting:"
        )
        return

    # Havolani avtomat yaratamiz
    board_id = initiative.get("boardId")
    project_url = f"https://openbudget.uz/boards/initiatives/{board_id}/details?initiativeId={project_id}"

    # FSM ma'lumotlarini yangilaymiz
    await state.update_data(
        project_id=project_id,
        project_url=project_url,
        project_uuid=initiative.get("id"),  # Loyihaning haqiqiy UUID kaliti
        initiative_title=initiative.get("boardTitle", "Tashabbusli Budjet"),
        region_name=initiative.get("regionName", ""),
        district_name=initiative.get("districtName", ""),
        quarter_name=initiative.get("quarterName", ""),
        category_name=initiative.get("categoryName", ""),
        vote_count=initiative.get("voteCount", 0),
        description=initiative.get("description", "")
    )

    await state.set_state(AdminStates.WAITING_FOR_PROJECT_CONFIRM)

    # Loyiha ma'lumotlarini chiroyli ko'rsatamiz
    details_text = (
        "📋 <b>Loyiha ma'lumotlari topildi:</b>\n\n"
        f"📅 <b>Mavsum:</b> {html.escape(str(initiative.get('boardTitle', 'Tashabbusli Budjet')))}\n"
        f"🏢 <b>Hudud:</b> {html.escape(str(initiative.get('regionName', '')))}, {html.escape(str(initiative.get('districtName', '')))}\n"
        f"🏡 <b>Mahalla:</b> {html.escape(str(initiative.get('quarterName', '')))}\n"
        f"📂 <b>Kategoriya:</b> {html.escape(str(initiative.get('categoryName', '')))}\n"
        f"🗳️ <b>Ovozlar soni:</b> {initiative.get('voteCount', 0)} ta\n"
        f"📝 <b>Tavsif:</b> {html.escape(str(initiative.get('description', ''))[:300])}...\n\n"
        f"📌 <b>Loyiha ID:</b> <code>{project_id}</code>\n\n"
        "Loyiha ma'lumotlari to'g'rimi? Tasdiqlasangiz, loyiha botga qo'shiladi va faollashtiriladi."
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="admin_confirm_proj_add"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel_proj_add")
            ]
        ]
    )

    await message.answer(details_text, reply_markup=confirm_keyboard, parse_mode="HTML")


@router.callback_query(AdminStates.WAITING_FOR_PROJECT_CONFIRM, F.data == "admin_confirm_proj_add")
async def process_admin_confirm_project_add(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    project_id = data.get("project_id")
    project_uuid = data.get("project_uuid")
    project_url = data.get("project_url")

    async with async_session() as db:
        # Loyihani qo'shish (project_id o'rniga UUID ni saqlaymiz!)
        await crud.add_project(
            db=db,
            project_id=project_uuid,
            project_url=project_url
        )
        # Faollashtirish
        await crud.activate_project(db=db, project_id=project_uuid)

    await state.clear()
    await callback.message.edit_text(
        f"✅ Yangi loyiha muvaffaqiyatli qo'shildi va faollashtirildi!\n\n"
        f"📌 <b>ID:</b> <code>{html.escape(str(project_id))}</code>\n"
        f"🔑 <b>UUID:</b> <code>{html.escape(str(project_uuid))}</code>\n"
        f"🔗 <b>Havola:</b> {html.escape(str(project_url))}",
        parse_mode="HTML"
    )
    # Asosiy menyuni chiqarish
    await callback.message.answer("Boshqaruv menyusi:", reply_markup=reply.get_admin_menu(callback.from_user.id))
    await callback.answer()


@router.callback_query(AdminStates.WAITING_FOR_PROJECT_CONFIRM, F.data == "admin_cancel_proj_add")
async def process_admin_cancel_project_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Loyiha qo'shish jarayoni bekor qilindi.")
    await callback.message.answer("Boshqaruv menyusi:", reply_markup=reply.get_admin_menu(callback.from_user.id))
    await callback.answer()
@router.callback_query(F.data.startswith("admin_proj_view_"))
async def process_admin_project_view(callback: CallbackQuery):
    """Loyiha tafsilotlarini ko'rish va boshqarish"""
    project_id = callback.data.replace("admin_proj_view_", "", 1)
    async with async_session() as db:
        result = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.project_id == project_id))
        project = result.scalar_one_or_none()

    if not project:
        await callback.answer("❌ Loyiha topilmadi.", show_alert=True)
        return

    from keyboards.inline import _get_display_id
    display_id = _get_display_id(project.project_id, project.project_url)

    text = (
        f"📂 <b>Loyiha ma'lumotlari:</b>\n\n"
        f"📌 <b>ID:</b> <code>{html.escape(display_id)}</code>\n"
        f"🔑 <b>UUID:</b> <code>{html.escape(project.project_id)}</code>\n"
        f"🔗 <b>Havola:</b> {html.escape(project.project_url)}\n"
        f"⚙️ <b>Holati:</b> {'🟢 Faol' if project.is_active else '🔴 Faolsiz'}\n\n"
        f"Quyidagi amallardan birini tanlang:"
    )
    await callback.message.edit_text(text, reply_markup=inline.get_project_manage_keyboard(project), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_proj_activate_"))
async def process_admin_project_activate(callback: CallbackQuery):
    """Loyihani faollashtirish (boshqa barcha loyihalarni faolsizlantiradi)"""
    project_id = callback.data.replace("admin_proj_activate_", "", 1)
    async with async_session() as db:
        await crud.activate_project(db, project_id)
    await callback.answer("🟢 Loyiha faollashtirildi!", show_alert=True)
    await process_admin_project_view(callback)

@router.callback_query(F.data.startswith("admin_proj_deactivate_"))
async def process_admin_project_deactivate(callback: CallbackQuery):
    """Loyihani faolsizlantirish (barcha loyihalarni faolsizlantiradi)"""
    async with async_session() as db:
        await crud.deactivate_all_projects(db)
    await callback.answer("🔴 Loyiha faolsizlantirildi!", show_alert=True)
    await process_admin_project_view(callback)

@router.callback_query(F.data == "admin_proj_deactivate_all")
async def process_admin_project_deactivate_all(callback: CallbackQuery, state: FSMContext):
    """Barcha loyihalarni faolsizlantirish"""
    async with async_session() as db:
        await crud.deactivate_all_projects(db)
    await callback.answer("🔴 Barcha loyihalar faolsizlantirildi!", show_alert=True)
    await show_projects_list(callback, state)

@router.callback_query(F.data.startswith("admin_proj_delete_"))
async def process_admin_project_delete(callback: CallbackQuery, state: FSMContext):
    """Loyihani o'chirish"""
    project_id = callback.data.replace("admin_proj_delete_", "", 1)
    
    # O'chirishdan oldin srazi hisobot faylini tayyorlab yuboramiz
    await send_project_report(callback, project_id, is_delete=True)
    
    async with async_session() as db:
        await crud.delete_project(db, project_id)
    await callback.answer("🗑️ Loyiha o'chirildi va hisoboti yuborildi!", show_alert=True)
    await show_projects_list(callback, state)

# --- 2. Referal mukofot narxini o'zgartirish ---

@router.message(F.text.contains("Ovoz mukofoti"))
async def admin_change_voter_reward(message: Message, state: FSMContext):
    """Ovoz bergan foydalanuvchining o'ziga beriladigan shaxsiy mukofotni o'zgartirish"""
    await state.set_state(AdminStates.WAITING_FOR_VOTER_REWARD)
    await message.answer(
        "💵 Ovoz bergan foydalanuvchining o'ziga beriladigan yangi mukofot summasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 1000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminStates.WAITING_FOR_VOTER_REWARD, F.text)
async def process_admin_voter_reward(message: Message, state: FSMContext):
    price_text = message.text.strip()
    if price_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    try:
        price = float(price_text)
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Noto'g'ri qiymat. Iltimos, musbat raqam kiriting:")
        return

    async with async_session() as db:
        await crud.update_project_settings(db=db, voter_reward=price)

    await state.clear()
    await message.answer(
        f"✅ Ovoz mukofoti narxi muvaffaqiyatli o'zgartirildi!\n"
        f"💵 Yangi narx: <b>{price} so'm</b>",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="HTML"
    )

@router.message(F.text.contains("Referal mukofoti"))
async def admin_change_price(message: Message, state: FSMContext):
    """Taklif qiluvchiga (referal) beriladigan mukofotni o'zgartirish"""
    await state.set_state(AdminStates.WAITING_FOR_REFERRAL_PRICE)
    await message.answer(
        "💵 Taklif qiluvchiga (referal) beriladigan yangi mukofot summasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 1500)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminStates.WAITING_FOR_REFERRAL_PRICE, F.text)
async def process_admin_price(message: Message, state: FSMContext):
    price_text = message.text.strip()
    if price_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    try:
        price = float(price_text)
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Noto'g'ri qiymat. Iltimos, musbat raqam kiriting:")
        return

    async with async_session() as db:
        await crud.update_project_settings(db=db, referral_price=price)

    await state.clear()
    await message.answer(
        f"✅ Referal mukofoti narxi muvaffaqiyatli o'zgartirildi!\n"
        f"💵 Yangi narx: <b>{price} so'm</b>",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="HTML"
    )

# --- 3. Minimal pul yechish chegarasini o'zgartirish ---

@router.message(F.text.contains("Min. Pul yechish") | F.text.contains("yechish"))
async def admin_change_min_withdraw(message: Message, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_MIN_WITHDRAWAL)
    await message.answer(
        "💳 Minimal pul yechib olish chegarasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 10000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminStates.WAITING_FOR_MIN_WITHDRAWAL, F.text)
async def process_admin_min_withdraw(message: Message, state: FSMContext):
    min_text = message.text.strip()
    if min_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    try:
        min_withdrawal = float(min_text)
        if min_withdrawal < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Noto'g'ri qiymat. Iltimos, musbat raqam kiriting:")
        return

    async with async_session() as db:
        await crud.update_project_settings(db=db, min_withdrawal=min_withdrawal)

    await state.clear()
    await message.answer(
        f"✅ Minimal pul yechib olish chegarasi muvaffaqiyatli o'zgartirildi!\n"
        f"💳 Yangi chegara: <b>{min_withdrawal} so'm</b>",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="HTML"
    )

# --- 3.5. Maxfiy kanal sozlamalarini o'zgartirish ---

@router.message(F.text.contains("Maxfiy kanal"))
@router.callback_query(F.data == "admin_change_channel")
async def admin_change_channel(message_or_callback, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_CHANNEL_USERNAME)
    
    async with async_session() as db:
        project_settings = await crud.get_project_settings(db)
        current_channel = project_settings.channel_username or "Hozircha sozlanmagan"

    text = (
        f"🔒 <b>Maxfiy kanal sozlamalari</b>\n\n"
        f"Joriy kanal: <code>{html.escape(str(current_channel))}</code>\n\n"
        f"Yangi kanal username yoki to'liq taklif havolasini kiriting (masalan: <code>@mening_kanalim</code> yoki <code>https://t.me/+abcde</code>):"
    )
    
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=reply.get_cancel_keyboard(), parse_mode="HTML")
    else:
        await message_or_callback.message.answer(text, reply_markup=reply.get_cancel_keyboard(), parse_mode="HTML")
        await message_or_callback.answer()

@router.message(AdminStates.WAITING_FOR_CHANNEL_USERNAME, F.text)
async def process_admin_channel_username(message: Message, state: FSMContext):
    channel_text = message.text.strip()
    if channel_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    # Kiruvchi ma'lumotni tozalaymiz
    if channel_text.startswith("@"):
        channel_text = channel_text[1:]

    async with async_session() as db:
        await crud.update_project_settings(db=db, channel_username=channel_text)

    await state.clear()
    await message.answer(
        f"✅ Maxfiy kanal muvaffaqiyatli o'zgartirildi!\n"
        f"🔒 Yangi kanal: <code>{html.escape(channel_text)}</code>",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="HTML"
    )

# --- 4. Statistikalarni ko'rish ---


@router.message(F.text.contains("Statistika"))
async def admin_statistics(message: Message):
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        active_project_id = active_project.project_id if active_project else "ulanish_mavjud_emas"
        
        stats = await crud.get_admin_stats(db, active_project_id)

    # Tarixiy ro'yxatni tuzish
    history_lines = []
    for h in stats["history_stats"]:
        status_star = "⭐️" if h["project_id"] == active_project_id else "•"
        history_lines.append(f"{status_star} Loyiha <code>{html.escape(str(h['project_id']))}</code>: {h['votes_count']} ta ovoz")
    
    history_text = "\n".join(history_lines) if history_lines else "Tarixiy ovozlar mavjud emas."

    stats_text = (
        f"📊 <b>Bot Statistikasi:</b>\n\n"
        f"👥 Botdagi jami a'zolar: <b>{stats['total_users']} ta</b>\n"
        f"🗳️ Joriy loyihadagi ovozlar (<code>{html.escape(str(active_project_id))}</code>): <b>{stats['current_votes']} ta</b>\n\n"
        f"📈 <b>Tarixiy ovozlar ro'yxati (Loyihalar bo'yicha):</b>\n"
        f"{history_text}"
    )
    await message.answer(stats_text, reply_markup=reply.get_admin_menu(message.from_user.id), parse_mode="HTML")

# --- 5. Pul Yechishni Tasdiqlash / Rad etish Callback Handler ---

# --- 5. Pul Yechishni Tasdiqlash / Rad etish Callback Handler ---

def _get_card_meta(card_raw: str):
    card = card_raw or "—"
    card_type = ""
    if card.startswith("8600") or card.startswith("5614"):
        card_type = " (Uzcard)"
    elif card.startswith("9860"):
        card_type = " (Humo)"
    elif card.startswith("4"):
        card_type = " (Visa)"
    elif card.startswith("5"):
        card_type = " (Mastercard)"
        
    masked_card = f"{card[:4]} •••• •••• {card[-4:]}" if len(card) >= 16 else (f"{card[:4]} ••••" if len(card) >= 4 else "—")
    return masked_card, card_type

def _build_payout_text(withdrawal, user, masked_card, card_type, date_str):
    if user and user.username:
        user_display = f"@{user.username} (<code>ID {withdrawal.telegram_id}</code>)"
    elif user and user.full_name:
        user_display = f"<a href='tg://user?id={withdrawal.telegram_id}'>{html.escape(user.full_name)}</a> (<code>ID {withdrawal.telegram_id}</code>)"
    else:
        user_display = f"<code>ID {withdrawal.telegram_id}</code>"
        
    return (
        f"💸 <b>YANGI TO'LOV AMALGA OSHIRILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧾 <b>To'lov ID:</b> <code>#{withdrawal.id}</code>\n"
        f"👤 <b>Qabul qiluvchi:</b> {user_display}\n"
        f"💰 <b>To'langan summa:</b> <b>{int(withdrawal.amount):,} so'm</b>\n"
        f"💳 <b>Hisob (karta):</b> <code>{masked_card}</code>{card_type}\n"
        f"📅 <b>Vaqt:</b> {date_str}\n"
        f"🟢 <b>Holati:</b> Muvaffaqiyatli to'landi ✅\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Siz ham ovoz bering va har bir ovoz uchun pul mukofotini oling!</b>"
    )

@router.callback_query(F.data.startswith("approve_"))
async def process_approve_withdraw(callback: CallbackQuery, state: FSMContext):
    withdraw_id = int(callback.data.split("_")[1])
    
    async with async_session() as db:
        withdrawal = await crud.get_withdrawal(db, withdraw_id)
        if not withdrawal or withdrawal.status != WithdrawalStatus.PENDING:
            await callback.answer("❌ Bu so'rov allaqachon ko'rib chiqilgan yoki topilmadi.", show_alert=True)
            return
        user = await crud.get_user(db, withdrawal.telegram_id)

    # State ga saqlab olamiz va chek rasmini so'raymiz
    await state.update_data(
        pending_wd_id=withdraw_id,
        admin_chat_id=callback.message.chat.id,
        admin_msg_id=callback.message.message_id,
        orig_html=callback.message.html_text
    )
    await state.set_state(AdminStates.WAITING_FOR_WITHDRAWAL_RECEIPT)
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Cheksiz tasdiqlash", callback_data=f"nocheck_{withdraw_id}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_receipt_upload")]
    ])
    
    u_display = f"@{user.username}" if user and user.username else f"ID: {withdrawal.telegram_id}"
    await callback.message.reply(
        f"🧾 <b>To'lov chekini (skrinshotini) yuboring:</b>\n\n"
        f"👤 Foydalanuvchi: <b>{u_display}</b> (<code>{withdrawal.telegram_id}</code>)\n"
        f"💰 Summa: <b>{withdrawal.amount:,} so'm</b>\n"
        f"💳 Karta: <code>{withdrawal.card_number}</code>\n\n"
        f"<i>Iltimos, kartaga pul o'tkazilgan chek skrinshotini (rasm sifatida) shu yerga yuboring:</i>",
        reply_markup=skip_kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_receipt_upload")
async def process_cancel_receipt_upload(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Bekor qilindi.")

@router.message(AdminStates.WAITING_FOR_WITHDRAWAL_RECEIPT, F.photo)
async def process_withdrawal_receipt_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    withdraw_id = data.get("pending_wd_id")
    admin_msg_id = data.get("admin_msg_id")
    admin_chat_id = data.get("admin_chat_id")
    orig_html = data.get("orig_html", "")
    
    photo_file_id = message.photo[-1].file_id
    
    async with async_session() as db:
        withdrawal = await crud.approve_withdrawal(db, withdraw_id)
        if not withdrawal:
            await state.clear()
            await message.answer("❌ Bu so'rov allaqachon tasdiqlangan yoki topilmadi.")
            return
            
        settings_db = await crud.get_project_settings(db)
        payouts_channel = settings_db.payouts_channel or getattr(settings, "PAYOUTS_CHANNEL", "")
        user = await crud.get_user(db, withdrawal.telegram_id)
        
    await state.clear()
    
    # 1. Admin xabarini yangilaymiz
    if admin_msg_id and admin_chat_id:
        try:
            admin_username = html.escape(str(message.from_user.username or message.from_user.id))
            await message.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=admin_msg_id,
                text=f"{orig_html}\n\n✅ <b>Tasdiqlandi (Chek biriktirildi)!</b> (Admin: @{admin_username})",
                parse_mode="HTML"
            )
        except Exception:
            pass
            
    # 2. Foydalanuvchiga chek bilan xabar
    masked_card, card_type = _get_card_meta(withdrawal.card_number)
    user_caption = (
        f"✅ <b>Sizning pul yechish so'rovingiz tasdiqlandi!</b>\n\n"
        f"💰 <b>Summa:</b> <code>{int(withdrawal.amount):,}</code> so'm\n"
        f"💳 <b>Karta:</b> <code>{masked_card}</code>{card_type}\n\n"
        f"🧾 <b>To'lov chekingiz yuqoridagi rasmda.</b> Hisobingizni tekshirishingiz mumkin! 🚀"
    )
    try:
        await message.bot.send_photo(
            chat_id=withdrawal.telegram_id,
            photo=photo_file_id,
            caption=user_caption,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Foydalanuvchiga chek yuborishda xatolik: {e}")
        
    # 3. To'lovlar kanaliga chek (rasm) bilan yuborish
    if payouts_channel:
        try:
            from datetime import datetime
            masked_card, card_type = _get_card_meta(withdrawal.card_number)
            date_str = datetime.now().strftime("%d.%m.%Y • %H:%M")
            pay_text = _build_payout_text(withdrawal, user, masked_card, card_type, date_str)
            
            bot_info = await message.bot.get_me()
            bot_username = bot_info.username
            channel_kb = None
            if bot_username:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                channel_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🗳️ Ovoz berish va pul ishlash", url=f"https://t.me/{bot_username}")
                ]])
                
            await message.bot.send_photo(
                chat_id=payouts_channel,
                photo=photo_file_id,
                caption=pay_text,
                reply_markup=channel_kb,
                parse_mode="HTML"
            )
            logger.info(f"Asosiy botdan to'lov kanaliga ({payouts_channel}) chek bilan xabar yuborildi.")
        except Exception as e:
            logger.error(f"To'lov kanaliga chek yuborishda xatolik: {e}")
            
    await message.answer("✅ <b>To'lov cheki bilan kanalga va foydalanuvchiga muvaffaqiyatli yuborildi!</b>", parse_mode="HTML")

@router.callback_query(F.data.startswith("nocheck_"))
async def process_approve_nocheck(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    withdraw_id = int(callback.data.split("_")[1])
    
    payouts_channel_db = None
    async with async_session() as db:
        withdrawal = await crud.approve_withdrawal(db, withdraw_id)
        if not withdrawal:
            await callback.answer("❌ Bu so'rov allaqachon tasdiqlangan, rad etilgan yoki topilmadi.", show_alert=True)
            return
            
        settings_db = await crud.get_project_settings(db)
        payouts_channel_db = settings_db.payouts_channel
        user = await crud.get_user(db, withdrawal.telegram_id)

    # Chek so'rov xabarini o'chirish
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Foydalanuvchiga xabar yuborish
    user_message = (
        f"✅ <b>Sizning pul yechish so'rovingiz tasdiqlandi!</b>\n\n"
        f"💰 Summa: <code>{withdrawal.amount:,}</code> so'm\n"
        f"💳 Karta raqamiga yuborildi. Hisobingizni tekshirishingiz mumkin."
    )
    try:
        await callback.bot.send_message(chat_id=withdrawal.telegram_id, text=user_message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Tasdiqlanganlik haqida foydalanuvchiga xabar yuborishda xatolik: {e}")

    # To'lovlar kanaliga xabar yuborish
    payout_channel = payouts_channel_db or getattr(settings, "PAYOUTS_CHANNEL", "")
    if payout_channel:
        try:
            from datetime import datetime
            masked_card, card_type = _get_card_meta(withdrawal.card_number)
            date_str = datetime.now().strftime("%d.%m.%Y • %H:%M")
            pay_text = _build_payout_text(withdrawal, user, masked_card, card_type, date_str)
            
            bot_info = await callback.bot.get_me()
            bot_username = bot_info.username
            channel_kb = None
            if bot_username:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                channel_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🗳️ Ovoz berish va pul ishlash", url=f"https://t.me/{bot_username}")
                ]])
                
            await callback.bot.send_message(
                chat_id=payout_channel,
                text=pay_text,
                reply_markup=channel_kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"To'lov kanaliga xabar yuborishda xatolik: {e}")
            
    await callback.answer("Pul yechish tasdiqlandi (cheksiz).", show_alert=True)

# --- 📋 Hisobot chiqarish handlers ---

@router.message(F.text.contains("Batafsil Hisobot") | F.text.contains("Hisobot"))
async def admin_report_select(message: Message, state: FSMContext):
    """Admin '📊 Batafsil Hisobot' tugmasini bosganda hisobot menyusini chiqaradi va ID so'raydi"""
    await state.set_state(AdminStates.WAITING_FOR_USER_REPORT_QUERY)
    
    async with async_session() as db:
        projects = await crud.get_all_projects_with_votes(db)
        
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = [
        [InlineKeyboardButton(text="📑 Barcha foydalanuvchilar to'liq hisoboti (Excel/CSV)", callback_data="admin_report_all_users")]
    ]
    
    if projects:
        for p in projects:
            buttons.append([InlineKeyboardButton(text=f"🗳️ Loyiha #{p} hisoboti (CSV)", callback_data=f"report_proj_{p}")])
            
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        "📊 <b>Batafsil Hisobotlar Bo'limi</b>\n\n"
        "🔍 <b>Foydalanuvchi hisobotini ko'rish uchun:</b>\n"
        "Uning <b>Telegram ID</b> raqamini (yoki <code>@username</code>) pastga yozib yuboring.\n\n"
        "👇 Yoki barcha foydalanuvchilarning umumiy to'liq hisobotini quyidagi tugma orqali yuklab oling:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.message(AdminStates.WAITING_FOR_USER_REPORT_QUERY, F.text)
async def process_user_report_query(message: Message, state: FSMContext):
    text = message.text.strip()
    
    # Agar admin boshqa menyu tugmasini bosgan bo'lsa
    if text in ("📊 Statistika", "🔒 Maxfiy kanal", "📢 Reklama yuborish", "🔙 Asosiy menyu", "🔑 API Web App", "📊 Batafsil Hisobot"):
        await state.clear()
        if text.startswith("📊 Stat"):
            return await admin_stats(message)
        elif "Maxfiy" in text:
            return await admin_secret_channel(message)
        elif "Reklama" in text:
            return await admin_broadcast_prompt(message, state)
        elif "Asosiy" in text:
            return await admin_back_to_user(message)
        return
        
    async with async_session() as db:
        user = await crud.get_user_by_query(db, text)
        if not user:
            await message.answer(
                f"❌ <code>{html.escape(text)}</code> bo'yicha hech qanday foydalanuvchi topilmadi.\n\n"
                "Iltimos, to'g'ri <b>Telegram ID</b> (masalan: <code>7505685720</code>) yoki <b>@username</b> kiriting:",
                parse_mode="HTML"
            )
            return
            
        data = await crud.get_user_detailed_report(db, user.telegram_id)
        
    votes = data["votes"]
    votes_lines = []
    for idx, v in enumerate(votes[:10], 1):
        v_date = v.created_at.strftime("%d.%m.%Y %H:%M") if v.created_at else "—"
        votes_lines.append(f"{idx}. <code>{v.phone_number}</code> — Loyiha: <code>{v.project_id}</code> ({v_date})")
        
    votes_text = "\n".join(votes_lines) if votes_lines else "<i>Ovozlar mavjud emas</i>"
    if len(votes) > 10:
        votes_text += f"\n<i>...va yana {len(votes) - 10} ta ovoz</i>"
        
    u_name = html.escape(user.full_name or "—")
    u_username = f"@{user.username}" if user.username else "<i>mavjud emas</i>"
    u_created = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "—"
    
    invited_info = f"<code>ID {user.invited_by}</code>" if user.invited_by else "<i>To'g'ridan-to'g'ri (Referalsiz)</i>"
    
    rep_text = (
        f"👤 <b>Foydalanuvchi Hisoboti:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>\n"
        f"👤 <b>Ism:</b> {u_name}\n"
        f"🔗 <b>Username:</b> {u_username}\n"
        f"💰 <b>Joriy Balans:</b> <b>{int(user.balance):,} so'm</b>\n"
        f"👥 <b>Taklif qilgan a'zolari:</b> <b>{data['referrals_count']} ta</b>\n"
        f"🗳️ <b>Referallari bergan ovozlar:</b> <b>{data['referral_votes']} ta</b>\n"
        f"💳 <b>Jami yechib olgan puli:</b> <b>{data['total_withdrawn']:,} so'm</b>\n"
        f"🤝 <b>Kim taklif qilgan:</b> {invited_info}\n"
        f"📅 <b>Ro'yxatdan o'tgan:</b> {u_created}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗳️ <b>O'zi bergan muvaffaqiyatli ovozlar ({len(votes)} ta):</b>\n"
        f"{votes_text}\n"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Ushbu foydalanuvchi ovozlarini yuklab olish (CSV)", callback_data=f"admin_rep_user_csv_{user.telegram_id}")],
        [InlineKeyboardButton(text="📑 Barcha foydalanuvchilar to'liq hisoboti (CSV)", callback_data="admin_report_all_users")]
    ])
    
    await message.answer(rep_text, reply_markup=user_kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_report_all_users")
async def process_admin_report_all_users(callback: CallbackQuery):
    waiting_msg = await callback.message.answer("🔄 <b>Barcha foydalanuvchilar to'liq hisoboti tayyorlanmoqda, kuting...</b>", parse_mode="HTML")
    
    async with async_session() as db:
        users_report = await crud.get_all_users_full_report(db)
        
    if not users_report:
        await waiting_msg.edit_text("❌ Foydalanuvchilar topilmadi.")
        return
        
    output = io.StringIO()
    output.write('\ufeff')
    
    writer = csv.writer(output, delimiter=',')
    writer.writerow([
        "Telegram ID",
        "Ism Familiya",
        "Username",
        "Joriy Balans (UZS)",
        "O'zi Bergan Ovozlar",
        "Taklif Qilgan Referallar Soni",
        "Referallari Bergan Ovozlar",
        "Jami Yechib Olgan Puli (UZS)",
        "Kim Taklif Qilgan (ID)",
        "Ro'yxatdan O'tgan Sana"
    ])
    
    for r in users_report:
        writer.writerow([
            r["telegram_id"],
            r["full_name"],
            r["username"],
            r["balance"],
            r["votes_count"],
            r["referrals_count"],
            r["referral_votes"],
            r["total_withdrawn"],
            r["invited_by"],
            r["created_at"]
        ])
        
    csv_bytes = output.getvalue().encode('utf-8')
    output.close()
    
    file_input = BufferedInputFile(
        file=csv_bytes,
        filename="barcha_foydalanuvchilar_hisoboti.csv"
    )
    
    try:
        await waiting_msg.delete()
    except Exception:
        pass
        
    await callback.message.answer_document(
        document=file_input,
        caption=(
            f"📑 <b>Barcha foydalanuvchilar to'liq hisoboti!</b>\n\n"
            f"👥 Jami foydalanuvchilar soni: <b>{len(users_report)} ta</b>\n"
            f"📅 Sana: <i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
        ),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("admin_rep_user_csv_"))
async def process_admin_rep_user_csv(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    
    async with async_session() as db:
        user = await crud.get_user(db, uid)
        data = await crud.get_user_detailed_report(db, uid)
        
    if not data or not user:
        await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return
        
    votes = data["votes"]
    if not votes:
        await callback.answer("Ushbu foydalanuvchida hali muvaffaqiyatli ovozlar yo'q.", show_alert=True)
        return
        
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=',')
    writer.writerow([
        "Telegram ID",
        "Ism Familiya",
        "Username",
        "Telefon Raqami",
        "Loyiha ID",
        "Holati",
        "Ovoz Berilgan Sana"
    ])
    
    for v in votes:
        writer.writerow([
            user.telegram_id,
            user.full_name or "—",
            f"@{user.username}" if user.username else "—",
            v.phone_number,
            v.project_id,
            v.status.value if hasattr(v.status, 'value') else str(v.status),
            v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "—"
        ])
        
    csv_bytes = output.getvalue().encode('utf-8')
    output.close()
    
    file_input = BufferedInputFile(
        file=csv_bytes,
        filename=f"ovozlar_user_{uid}.csv"
    )
    
    await callback.message.answer_document(
        document=file_input,
        caption=f"📥 <code>{uid}</code> IDli foydalanuvchining muvaffaqiyatli ovozlari hisoboti ({len(votes)} ta).",
        parse_mode="HTML"
    )
    await callback.answer()

async def send_project_report(message_or_callback, project_id: str, is_delete: bool = False):
    """Loyiha bo'yicha CSV hisobotini yaratib adminga jo'natadi"""
    waiting_txt = "🔄 O'chirishdan oldin hisobot fayli tayyorlanmoqda..." if is_delete else "🔄 Hisobot fayli tayyorlanmoqda, kuting..."
    waiting_msg = None
    if isinstance(message_or_callback, Message):
        waiting_msg = await message_or_callback.answer(waiting_txt)
    else:
        waiting_msg = await message_or_callback.message.answer(waiting_txt)
    
    async with async_session() as db:
        report_data = await crud.get_votes_report(db, project_id)

    if not report_data:
        msg = f"ℹ️ Loyiha `{project_id}` bo'yicha hech qanday muvaffaqiyatli ovozlar topilmadi (hisobot fayli bo'sh)."
        if waiting_msg:
            try:
                await waiting_msg.edit_text(msg)
            except Exception:
                pass
        else:
            if isinstance(message_or_callback, Message):
                await message_or_callback.answer(msg)
            else:
                await message_or_callback.message.answer(msg)
        return

    # CSV faylini xotirada (in-memory) yaratamiz
    output = io.StringIO()
    output.write('\ufeff')
    
    writer = csv.writer(output, delimiter=',')
    writer.writerow([
        "Ism Familiya", 
        "Telegram Username", 
        "Telegram ID", 
        "Ovoz Berilgan Telefon Raqami", 
        "Ovoz Berilgan Sana va Vaqt"
    ])
    
    for row in report_data:
        writer.writerow([
            row["full_name"],
            row["username"],
            row["telegram_id"],
            row["phone_number"],
            row["voted_at"]
        ])
    
    csv_bytes = output.getvalue().encode('utf-8')
    output.close()

    file_input = BufferedInputFile(
        file=csv_bytes,
        filename=f"hisobot_loyiha_{project_id}.csv"
    )

    if is_delete:
        caption_text = (
            f"🗑️ <b>Loyiha o'chirilishi munosabati bilan hisobot!</b>\n\n"
            f"📌 O'chirilgan Loyiha ID: <code>{html.escape(str(project_id))}</code>\n"
            f"👥 Jami to'plangan ovozlar soni: <b>{len(report_data)} ta</b>\n"
            f"⚠️ Ma'lumotlaringiz yo'qolmasligi uchun ushbu faylni saqlab qo'ying."
        )
    else:
        caption_text = (
            f"📋 <b>Loyiha bo'yicha hisobot tayyor!</b>\n\n"
            f"📌 Loyiha ID: <code>{html.escape(str(project_id))}</code>\n"
            f"👥 Muvaffaqiyatli ovozlar soni: <b>{len(report_data)} ta</b>\n"
            f"📂 Hujjat formati: Excel/CSV"
        )

    try:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer_document(document=file_input, caption=caption_text, parse_mode="HTML")
        else:
            await message_or_callback.message.answer_document(document=file_input, caption=caption_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Hisobot yuborishda xato: {e}")
        
    try:
        await waiting_msg.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("admin_report_"))
async def process_admin_report_download(callback: CallbackQuery):
    """Tanlangan loyiha bo'yicha CSV hisobot faylini yaratib jo'natadi"""
    project_id = callback.data.replace("admin_report_", "", 1)
    await send_project_report(callback, project_id, is_delete=False)
    await callback.answer()

@router.callback_query(F.data.startswith("reject_"))
async def process_reject_withdraw(callback: CallbackQuery):
    withdraw_id = int(callback.data.split("_")[1])
    
    async with async_session() as db:
        # Pul yechishni rad etish va mablag'ni qaytarish
        withdrawal = await crud.reject_withdrawal(db, withdraw_id)
        if not withdrawal:
            await callback.answer("❌ Bu so'rov allaqachon tasdiqlangan, rad etilgan yoki topilmadi.", show_alert=True)
            return

    # Admin xabarini yangilash (HTML formatida)
    original_html = callback.message.html_text
    updated_text = (
        f"{original_html}\n\n"
        f"❌ <b>Rad etildi!</b> (Admin: @{html.escape(str(callback.from_user.username or callback.from_user.id))})"
    )
    await callback.message.edit_text(text=updated_text, reply_markup=None, parse_mode="HTML")
    await callback.answer("Pul yechish rad etildi. Mablag' balansga qaytarildi.", show_alert=True)

    # Foydalanuvchiga xabar yuborish
    user_message = (
        f"❌ <b>Sizning pul yechish so'rovingiz rad etildi.</b>\n\n"
        f"💰 Summa: <code>{withdrawal.amount}</code> so'm qaytadan balansingizga qo'shildi."
    )
    try:
        await callback.bot.send_message(chat_id=withdrawal.telegram_id, text=user_message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Rad etilganlik haqida foydalanuvchiga (ID: {withdrawal.telegram_id}) xabar yuborishda xatolik: {e}")

@router.callback_query(F.data.startswith("reveal_card_"))
async def process_reveal_card(callback: CallbackQuery):
    """Faqat admin uchun: to'liq karta raqamini xususiy xabar sifatida yuborish"""
    withdraw_id = int(callback.data.replace("reveal_card_", "", 1))
    async with async_session() as db:
        withdrawal = await crud.get_withdrawal(db, withdraw_id)
    
    if not withdrawal:
        await callback.answer("❌ So'rov topilmadi.", show_alert=True)
        return

    # To'liq raqamni faqat callback yuborgan adminga shaxsiy xabar bilan yuboramiz
    full_card = withdrawal.card_number
    formatted = f"{full_card[:4]} {full_card[4:8]} {full_card[8:12]} {full_card[12:16]}"
    try:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=f"💳 <b>To'liq karta raqami (So'rov #{withdraw_id}):</b>\n<code>{formatted}</code>",
            parse_mode="HTML"
        )
        await callback.answer("💳 Karta raqami shaxsiy xabar sifatida yuborildi.", show_alert=True)
    except Exception as e:
        logger.error(f"Karta raqamini yuborishda xato: {e}")
        await callback.answer("❌ Xabar yuborishda xato yuz berdi.", show_alert=True)

# --- Admin inline menyu callback query handlers ---

@router.callback_query(F.data == "admin_proj_list")
async def admin_projects_list_callback(callback: CallbackQuery, state: FSMContext):
    """Inline orqali loyihalar ro'yxatiga qaytish"""
    await show_projects_list(callback, state)
    await callback.answer()

@router.callback_query(F.data == "admin_change_voter_reward")
async def admin_change_voter_reward_callback(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan ovoz mukofotini o'zgartirish"""
    await state.set_state(AdminStates.WAITING_FOR_VOTER_REWARD)
    await callback.message.answer(
        "💵 Ovoz bergan foydalanuvchining o'ziga beriladigan yangi mukofot summasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 1000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_change_referral_price")
async def admin_change_referral_price_callback(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan referal mukofotini o'zgartirish"""
    await state.set_state(AdminStates.WAITING_FOR_REFERRAL_PRICE)
    await callback.message.answer(
        "💵 Taklif qiluvchiga (referal) beriladigan yangi mukofot summasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 1500)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_change_min")
async def admin_change_min_withdraw_callback(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan minimal yechish summasini o'zgartirish oqimini boshlash"""
    await state.set_state(AdminStates.WAITING_FOR_MIN_WITHDRAWAL)
    await callback.message.answer(
        "💳 Minimal pul yechib olish chegarasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 10000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_view_stats")
async def admin_statistics_callback(callback: CallbackQuery):
    """Inline menyudan statistikalarni ko'rish"""
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        active_project_id = active_project.project_id if active_project else "ulanish_mavjud_emas"
        stats = await crud.get_admin_stats(db, active_project_id)

    history_lines = []
    for h in stats["history_stats"]:
        status_star = "⭐️" if h["project_id"] == active_project_id else "•"
        history_lines.append(f"{status_star} Loyiha <code>{html.escape(str(h['project_id']))}</code>: {h['votes_count']} ta ovoz")
    
    history_text = "\n".join(history_lines) if history_lines else "Tarixiy ovozlar mavjud emas."

    stats_text = (
        f"📊 <b>Bot Statistikasi:</b>\n\n"
        f"👥 Botdagi jami a'zolar: <b>{stats['total_users']} ta</b>\n"
        f"🗳️ Joriy loyihadagi ovozlar (<code>{html.escape(str(active_project_id))}</code>): <b>{stats['current_votes']} ta</b>\n\n"
        f"📈 <b>Tarixiy ovozlar ro'yxati (Loyihalar bo'yicha):</b>\n"
        f"{history_text}"
    )
    await callback.message.answer(stats_text, reply_markup=reply.get_admin_menu(callback.from_user.id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_select_report")
async def admin_report_select_callback(callback: CallbackQuery):
    """Inline menyudan hisoboti bor loyihalar ro'yxatini yuklash"""
    async with async_session() as db:
        projects = await crud.get_all_projects_with_votes(db)

    if not projects:
        await callback.message.answer("❌ Hozircha bot orqali muvaffaqiyatli ovoz berilgan loyihalar mavjud emas.")
        await callback.answer()
        return

    from keyboards import inline
    await callback.message.answer(
        "📋 <b>Hisobot yuklab olish uchun loyihani tanlang:</b>",
        reply_markup=inline.get_admin_projects_keyboard(projects),
        parse_mode="HTML"
    )
    await callback.answer()

# --- 📣 Reklama yuborish handlers ---

@router.message(F.text.contains("Reklama yuborish") | F.text.contains("Reklama"))
async def admin_broadcast_start(message: Message, state: FSMContext):
    """Barcha a'zolarga reklama yuborish jarayonini boshlash"""
    await state.set_state(AdminStates.WAITING_FOR_AD_TEXT)
    await message.answer(
        "📣 **Barcha foydalanuvchilarga reklama yuborish bo'limi**\n\n"
        "Iltimos, yubormoqchi bo'lgan reklama xabaringizni kiriting:\n"
        "Bu oddiy matn, rasm, video, audio yoki hujjat bo'lishi mumkin. Bot o'sha xabarni qanday bo'lsa, shundayligicha barcha a'zolarga nusxalab jo'natadi.",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminStates.WAITING_FOR_AD_TEXT)
async def process_admin_broadcast(message: Message, state: FSMContext):
    """Kiritilgan reklama xabarini barcha foydalanuvchilarga yuborish"""
    if message.text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu(message.from_user.id))
        return

    await state.clear()
    status_msg = await message.answer("⏳ Reklama yuborilishi boshlandi, kuting...")
    
    async with async_session() as db:
        user_ids = await crud.get_all_user_ids(db)
    
    if not user_ids:
        await status_msg.edit_text("❌ Botda foydalanuvchilar topilmadi.")
        return
    
    success_count = 0
    failed_count = 0
    
    # Telegram rate limitlaridan himoyalanish importlari
    from aiogram.exceptions import TelegramRetryAfter
    import asyncio

    # Barcha foydalanuvchilarga xabarni nusxalaymiz
    for user_id in user_ids:
        try:
            await message.copy_to(chat_id=user_id)
            success_count += 1
        except TelegramRetryAfter as e:
            # Agar limitga tushib qolsak, so'ralgan soniya kutamiz va qayta urinib ko'ramiz
            logger.warning(f"Telegram Flood Control! {e.retry_after} soniya kutilmoqda...")
            await asyncio.sleep(e.retry_after)
            try:
                await message.copy_to(chat_id=user_id)
                success_count += 1
            except Exception:
                failed_count += 1
        except Exception:
            failed_count += 1
        
        # Har bir so'rovdan keyin 50ms kechikish (soniyasiga 20 ta so'rov, Telegram limiti 30 msg/sec)
        await asyncio.sleep(0.05)
    
    await status_msg.answer(
        f"✅ **Reklama yuborish yakunlandi!**\n\n"
        f"🎉 Muvaffaqiyatli yuborildi: **{success_count} ta** foydalanuvchiga\n"
        f"❌ Yuborib bo'lmadi: **{failed_count} ta** (botni bloklaganlar)",
        reply_markup=reply.get_admin_menu(message.from_user.id),
        parse_mode="Markdown"
    )
    
    try:
        await status_msg.delete()
    except Exception:
        pass


# --- ⚙️ Sozlamalar handlers ---
# (Ushbu sozlamalar endi API Web App boshqaruv paneli ichiga ko'chirilgan)



