import io
import csv
import logging
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

@router.message(Command("admin"))
@router.message(F.text == "🔙 Asosiy menyu")
async def cmd_admin(message: Message, state: FSMContext):
    """Admin panelini ochish yoki foydalanuvchi rejimiga qaytish"""
    await state.clear()
    
    if message.text == "🔙 Asosiy menyu":
        await message.answer("Asosiy menyuga qaytdingiz.", reply_markup=reply.get_user_menu())
        return

    await message.answer(
        "🛠️ **Admin boshqaruv paneliga xush kelibsiz!**\n\n"
        "Quyidagi tugmalar orqali bot sozlamalarini boshqarishingiz mumkin:",
        reply_markup=reply.get_admin_menu(),
        parse_mode="Markdown"
    )

# --- Helper to show projects list ---

async def show_projects_list(message_or_callback, state: FSMContext):
    await state.clear()
    async with async_session() as db:
        projects = await crud.get_all_projects(db)
    
    text = (
        "📂 **Open Budget loyihalari ro'yxati**\n\n"
        "Quyidagi loyihalardan birini tanlab faollashtirishingiz, faolsizlantirishingiz yoki o'chirishingiz mumkin. "
        "Yoki yangi loyiha qo'shishingiz mumkin:\n\n"
        "*(Eslatma: Bir vaqtda faqat 1 ta loyiha faol bo'la oladi)*"
    )
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=inline.get_admin_projects_list_keyboard(projects), parse_mode="Markdown")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=inline.get_admin_projects_list_keyboard(projects), parse_mode="Markdown")

# --- 1. Loyihalar boshqaruvi handlers ---

@router.message(F.text == "📂 Loyihalar")
async def admin_projects_list_message(message: Message, state: FSMContext):
    """Admin '📂 Loyihalar' tugmasini bosganda loyihalar ro'yxatini chiqaradi"""
    await show_projects_list(message, state)

@router.message(AdminStates.WAITING_FOR_PROJECT_ID, F.text)
async def process_admin_project_id(message: Message, state: FSMContext):
    project_id = message.text.strip()
    if project_id == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu())
        return

    await state.update_data(project_id=project_id)
    await state.set_state(AdminStates.WAITING_FOR_PROJECT_URL)
    await message.answer(
        "🔗 Endi loyihaning to'liq **havolasini (URL)** kiriting:\n"
        "(Masalan: https://openbudget.uz/boards/initiatives/31/details?initiativeId=32541)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminStates.WAITING_FOR_PROJECT_URL, F.text)
async def process_admin_project_url(message: Message, state: FSMContext):
    project_url = message.text.strip()
    if project_url == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu())
        return

    if not project_url.startswith("http"):
        await message.answer("❌ Havola noto'g'ri. Havola 'http' yoki 'https' bilan boshlanishi kerak. Qayta kiriting:")
        return

    data = await state.get_data()
    project_id = data.get("project_id")

    async with async_session() as db:
        await crud.add_project(
            db=db,
            project_id=project_id,
            project_url=project_url
        )

    await state.clear()
    await message.answer(
        f"✅ Yangi loyiha muvaffaqiyatli qo'shildi!\n\n"
        f"📌 Loyiha ID: `{project_id}`\n"
        f"🔗 Havola: {project_url}",
        reply_markup=reply.get_admin_menu(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("admin_proj_view_"))
async def process_admin_project_view(callback: CallbackQuery):
    """Loyiha tafsilotlarini ko'rish va boshqarish"""
    project_id = callback.data.split("_")[3]
    async with async_session() as db:
        result = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.project_id == project_id))
        project = result.scalar_one_or_none()

    if not project:
        await callback.answer("❌ Loyiha topilmadi.", show_alert=True)
        return

    status_text = "🟢 Faol (Ishga tushirilgan)" if project.is_active else "🔴 Faolsiz (O'chirilgan)"
    text = (
        f"📂 **Loyiha tafsilotlari**\n\n"
        f"📌 **Loyiha ID:** `{project.project_id}`\n"
        f"🔗 **Havola:** {project.project_url}\n"
        f"⚡ **Holati:** {status_text}\n\n"
        f"Quyidagi amallardan birini tanlang:"
    )
    await callback.message.edit_text(text, reply_markup=inline.get_project_manage_keyboard(project), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_proj_activate_"))
async def process_admin_project_activate(callback: CallbackQuery):
    """Loyihani faollashtirish (boshqa barcha loyihalarni faolsizlantiradi)"""
    project_id = callback.data.split("_")[3]
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
    project_id = callback.data.split("_")[3]
    
    # O'chirishdan oldin srazi hisobot faylini tayyorlab yuboramiz
    await send_project_report(callback, project_id, is_delete=True)
    
    async with async_session() as db:
        await crud.delete_project(db, project_id)
    await callback.answer("🗑️ Loyiha o'chirildi va hisoboti yuborildi!", show_alert=True)
    await show_projects_list(callback, state)

# --- 2. Referal mukofot narxini o'zgartirish ---

@router.message(F.text == "💰 Mukofot narxi")
async def admin_change_price(message: Message, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_REFERRAL_PRICE)
    await message.answer(
        "💵 Har bir muvaffaqiyatli ovoz/taklif uchun beriladigan yangi summa qiymatini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 2000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminStates.WAITING_FOR_REFERRAL_PRICE, F.text)
async def process_admin_price(message: Message, state: FSMContext):
    price_text = message.text.strip()
    if price_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu())
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
        f"💵 Yangi narx: **{price} so'm**",
        reply_markup=reply.get_admin_menu(),
        parse_mode="Markdown"
    )

# --- 3. Minimal pul yechish chegarasini o'zgartirish ---

@router.message(F.text == "💸 Min. Pul yechish")
async def admin_change_min_withdraw(message: Message, state: FSMContext):
    await state.set_state(AdminStates.WAITING_FOR_MIN_WITHDRAWAL)
    await message.answer(
        "💳 Minimal pul yechib olish chegarasini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 10000)",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminStates.WAITING_FOR_MIN_WITHDRAWAL, F.text)
async def process_admin_min_withdraw(message: Message, state: FSMContext):
    min_text = message.text.strip()
    if min_text == "❌ Jarayonni bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu())
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
        f"💳 Yangi chegara: **{min_withdrawal} so'm**",
        reply_markup=reply.get_admin_menu(),
        parse_mode="Markdown"
    )

# --- 4. Statistikalarni ko'rish ---

@router.message(F.text == "📈 Statistika")
async def admin_statistics(message: Message):
    async with async_session() as db:
        active_project = await crud.get_active_project(db)
        active_project_id = active_project.project_id if active_project else "ulanish_mavjud_emas"
        
        stats = await crud.get_admin_stats(db, active_project_id)

    # Tarixiy ro'yxatni tuzish
    history_lines = []
    for h in stats["history_stats"]:
        status_star = "⭐️" if h["project_id"] == active_project_id else "•"
        history_lines.append(f"{status_star} Loyiha `{h['project_id']}`: {h['votes_count']} ta ovoz")
    
    history_text = "\n".join(history_lines) if history_lines else "Tarixiy ovozlar mavjud emas."

    stats_text = (
        f"📊 **Bot Statistikasi:**\n\n"
        f"👥 Botdagi jami a'zolar: **{stats['total_users']} ta**\n"
        f"🗳️ Joriy loyihadagi ovozlar (`{active_project_id}`): **{stats['current_votes']} ta**\n\n"
        f"📈 **Tarixiy ovozlar ro'yxati (Loyihalar bo'yicha):**\n"
        f"{history_text}"
    )
    await message.answer(stats_text, reply_markup=reply.get_admin_menu(), parse_mode="Markdown")

# --- 5. Pul Yechishni Tasdiqlash / Rad etish Callback Handler ---

@router.callback_query(F.data.startswith("approve_"))
async def process_approve_withdraw(callback: CallbackQuery):
    withdraw_id = int(callback.data.split("_")[1])
    
    async with async_session() as db:
        # Pul yechishni tasdiqlash
        withdrawal = await crud.approve_withdrawal(db, withdraw_id)
        if not withdrawal:
            await callback.answer("❌ Bu so'rov allaqachon tasdiqlangan, rad etilgan yoki topilmadi.", show_alert=True)
            return

    # Admin xabarini yangilash
    original_text = callback.message.text
    updated_text = (
        f"{original_text}\n\n"
        f"✅ **Tasdiqlandi!** (Admin: @{callback.from_user.username or callback.from_user.id})"
    )
    await callback.message.edit_text(text=updated_text, reply_markup=None)
    await callback.answer("Pul yechish tasdiqlandi.", show_alert=True)

    # Foydalanuvchiga xabar yuborish
    user_message = (
        f"✅ **Sizning pul yechish so'rovingiz tasdiqlandi!**\n\n"
        f"💰 Summa: {withdrawal.amount} so'm\n"
        f"💳 Karta raqamiga yuborildi. Hisobingizni tekshirishingiz mumkin."
    )
    try:
        await callback.bot.send_message(chat_id=withdrawal.telegram_id, text=user_message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Tasdiqlanganlik haqida foydalanuvchiga (ID: {withdrawal.telegram_id}) xabar yuborishda xatolik: {e}")

# --- 📋 Hisobot chiqarish handlers ---

@router.message(F.text == "📊 Batafsil Hisobot")
async def admin_report_select(message: Message):
    """Admin '📋 Hisobot' tugmasini bosganda ovozi bor loyihalar ro'yxatini inline tugma shaklida chiqaradi"""
    async with async_session() as db:
        projects = await crud.get_all_projects_with_votes(db)

    if not projects:
        await message.answer("❌ Hozircha bot orqali muvaffaqiyatli ovoz berilgan loyihalar mavjud emas.")
        return

    # Loyihalar ro'yxatini inline klaviatura shaklida chiqaramiz
    from keyboards import inline
    await message.answer(
        "📋 **Hisobot yuklab olish uchun loyihani tanlang:**",
        reply_markup=inline.get_admin_projects_keyboard(projects),
        parse_mode="Markdown"
    )

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
            f"🗑️ **Loyiha o'chirilishi munosabati bilan hisobot!**\n\n"
            f"📌 O'chirilgan Loyiha ID: `{project_id}`\n"
            f"👥 Jami to'plangan ovozlar soni: **{len(report_data)} ta**\n"
            f"⚠️ Ma'lumotlaringiz yo'qolmasligi uchun ushbu faylni saqlab qo'ying."
        )
    else:
        caption_text = (
            f"📋 **Loyiha bo'yicha hisobot tayyor!**\n\n"
            f"📌 Loyiha ID: `{project_id}`\n"
            f"👥 Muvaffaqiyatli ovozlar soni: **{len(report_data)} ta**\n"
            f"📂 Hujjat formati: Excel/CSV"
        )

    try:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer_document(document=file_input, caption=caption_text, parse_mode="Markdown")
        else:
            await message_or_callback.message.answer_document(document=file_input, caption=caption_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Hisobot yuborishda xato: {e}")
        
    try:
        await waiting_msg.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("admin_report_"))
async def process_admin_report_download(callback: CallbackQuery):
    """Tanlangan loyiha bo'yicha CSV hisobot faylini yaratib jo'natadi"""
    project_id = callback.data.split("_")[2]
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

    # Admin xabarini yangilash
    original_text = callback.message.text
    updated_text = (
        f"{original_text}\n\n"
        f"❌ **Rad etildi!** (Admin: @{callback.from_user.username or callback.from_user.id})"
    )
    await callback.message.edit_text(text=updated_text, reply_markup=None)
    await callback.answer("Pul yechish rad etildi. Mablag' balansga qaytarildi.", show_alert=True)

    # Foydalanuvchiga xabar yuborish
    user_message = (
        f"❌ **Sizning pul yechish so'rovingiz rad etildi.**\n\n"
        f"💰 Summa: {withdrawal.amount} so'm qaytadan balansingizga qo'shildi."
    )
    try:
        await callback.bot.send_message(chat_id=withdrawal.telegram_id, text=user_message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Rad etilganlik haqida foydalanuvchiga (ID: {withdrawal.telegram_id}) xabar yuborishda xatolik: {e}")

# --- Admin inline menyu callback query handlers ---

@router.callback_query(F.data == "admin_proj_list")
async def admin_projects_list_callback(callback: CallbackQuery, state: FSMContext):
    """Inline orqali loyihalar ro'yxatiga qaytish"""
    await show_projects_list(callback, state)
    await callback.answer()

@router.callback_query(F.data == "admin_change_price")
async def admin_change_price_callback(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan referal narxini o'zgartirish oqimini boshlash"""
    await state.set_state(AdminStates.WAITING_FOR_REFERRAL_PRICE)
    await callback.message.answer(
        "💵 Har bir muvaffaqiyatli ovoz/taklif uchun beriladigan yangi summa qiymatini kiriting (so'mda):\n"
        "(Faqat raqam kiriting, masalan: 2000)",
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
        history_lines.append(f"{status_star} Loyiha `{h['project_id']}`: {h['votes_count']} ta ovoz")
    
    history_text = "\n".join(history_lines) if history_lines else "Tarixiy ovozlar mavjud emas."

    stats_text = (
        f"📊 **Bot Statistikasi:**\n\n"
        f"👥 Botdagi jami a'zolar: **{stats['total_users']} ta**\n"
        f"🗳️ Joriy loyihadagi ovozlar (`{active_project_id}`): **{stats['current_votes']} ta**\n\n"
        f"📈 **Tarixiy ovozlar ro'yxati (Loyihalar bo'yicha):**\n"
        f"{history_text}"
    )
    await callback.message.answer(stats_text, reply_markup=reply.get_admin_menu(), parse_mode="Markdown")
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
        "📋 **Hisobot yuklab olish uchun loyihani tanlang:**",
        reply_markup=inline.get_admin_projects_keyboard(projects),
        parse_mode="Markdown"
    )
    await callback.answer()

# --- 📣 Reklama yuborish handlers ---

@router.message(F.text == "📣 Reklama yuborish")
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
        await message.answer("Amal bekor qilindi.", reply_markup=reply.get_admin_menu())
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
    
    # Barcha foydalanuvchilarga xabarni nusxalaymiz (formatting, rasmlar, knopkalari bilan birga ketadi)
    for user_id in user_ids:
        try:
            await message.copy_to(chat_id=user_id)
            success_count += 1
        except Exception:
            failed_count += 1
    
    await status_msg.answer(
        f"✅ **Reklama yuborish yakunlandi!**\n\n"
        f"🎉 Muvaffaqiyatli yuborildi: **{success_count} ta** foydalanuvchiga\n"
        f"❌ Yuborib bo'lmadi: **{failed_count} ta** (botni bloklaganlar)",
        reply_markup=reply.get_admin_menu(),
        parse_mode="Markdown"
    )
    
    try:
        await status_msg.delete()
    except Exception:
        pass
