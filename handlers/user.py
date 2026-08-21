import re
import logging
import html
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ChatJoinRequest

from config import settings
from database.session import async_session
from database import crud
from states.user_states import WithdrawStates
from keyboards import reply, inline

router = Router()
logger = logging.getLogger(__name__)

@router.message(CommandStart(), StateFilter("*"))
@router.message(Command("start"), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext):
    """
    Botga start berilganda ishlaydi. 
    Deep-linking orqali referal linkni (/start <referrer_id>) qo'llab-quvvatlaydi.
    """
    await state.clear()
    
    telegram_id = message.from_user.id
    username = message.from_user.username
    
    # Deep-link tekshirish
    invited_by = None
    command_args = message.text.split()
    if len(command_args) > 1:
        try:
            invited_by = int(command_args[1])
        except ValueError:
            pass

    async with async_session() as db:
        user, created = await crud.get_or_create_user(
            db=db,
            telegram_id=telegram_id,
            username=username,
            full_name=message.from_user.full_name,
            invited_by=invited_by
        )
        
        project_settings = await crud.get_project_settings(db)
        referral_price = project_settings.referral_price

    if created:
        welcome_text = (
            f"🗳️ <b>Open Budget — Ovoz Berish Tizimi</b>\n\n"
            f"Assalomu alaykum, <b>{html.escape(str(message.from_user.full_name))}</b>!\n\n"
            f"Bot orqali mahallangiz loyihasiga ovoz bering va daromad qiling!\n\n"
            f"💰 <b>Har bir ovoz uchun mukofot:</b> <b>{referral_price:,} so'm</b>\n\n"
            f"👇 Boshlash uchun quyidagi menyudan tanlang:"
        )
    else:
        welcome_text = (
            f"🗳️ <b>Open Budget — Ovoz Berish Tizimi</b>\n\n"
            f"Assalomu alaykum, <b>{html.escape(str(message.from_user.full_name))}</b>!\n\n"
            f"Qayta tashrifingizdan xursandmiz.\n"
            f"👇 Boshlash uchun quyidagi menyudan tanlang:"
        )

    await message.answer(
        welcome_text,
        reply_markup=reply.get_user_menu(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "user_registered_confirm")
async def process_user_registered_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    welcome_text = data.get("welcome_text")
    
    if not welcome_text:
        welcome_text = (
            f"👋 Assalomu alaykum, {html.escape(str(callback.from_user.full_name))}!\n\n"
            f"Quyidagi menyudan foydalanishingiz mumkin 👇"
        )

    await state.clear()
    await callback.message.edit_text("✅ Ro'yxatdan o'tganligingiz tasdiqlandi!")
    await callback.message.answer(welcome_text, reply_markup=reply.get_user_menu(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_balance")
async def cb_menu_balance(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = callback.from_user.id
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            user, _ = await crud.get_or_create_user(
                db=db, telegram_id=telegram_id,
                username=callback.from_user.username,
                full_name=callback.from_user.full_name
            )
        project_settings = await crud.get_project_settings(db)
        min_withdrawal = project_settings.min_withdrawal

    text = (
        f"👤 <b>Ism:</b> {html.escape(str(callback.from_user.full_name))}\n"
        f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
        f"💳 <b>Hamyon balansi:</b> {user.balance:,} so'm\n"
        f"👥 <b>Taklif mukofoti:</b> {user.total_referrals} ta referal\n\n"
        f"📌 Minimal yechib olish summasi: {min_withdrawal:,} so'm\n\n"
        f"Quyidagi variantlardan birini tanlang 👇"
    )
    await callback.message.answer(text, reply_markup=inline.get_withdrawal_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_referral")
async def cb_menu_referral(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = callback.from_user.id
    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={telegram_id}"
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        project_settings = await crud.get_project_settings(db)
        referral_price = project_settings.referral_price

    text = (
        f"🎁 <b>Sizning referal havolangiz:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Taklif qilgan a'zolaringiz: <b>{user.total_referrals if user else 0} ta</b>\n"
        f"💵 Do'stingiz muvaffaqiyatli ovoz bersa, balansingizga <b>{referral_price:,} so'm</b> qo'shiladi!"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_partnership")
async def cb_menu_partnership(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "🤝 **Hamkorlik bo'limiga xush kelibsiz!**\n\n"
        "Bu yerda siz o'zingizning shaxsiy Open Budget botingizni ishga tushirish uchun "
        "tayyor kodni yuklab olishingiz yoki u bilan ishlaydigan API kalitlarni sotib olishingiz mumkin.",
        reply_markup=inline.get_partnership_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = callback.from_user.id
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
    text = (
        f"👤 <b>Foydalanuvchi Profili</b>\n\n"
        f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
        f"👤 <b>Ism:</b> {html.escape(str(callback.from_user.full_name))}\n"
        f"💳 <b>Balans:</b> {user.balance if user else 0:,} so'm\n"
        f"👥 <b>Referallar soni:</b> {user.total_referrals if user else 0} ta"
    )
    await callback.message.answer(text, reply_markup=inline.get_user_inline_menu(), parse_mode="HTML")
    await callback.answer()

@router.message(F.text.contains("bekor qilish") | F.text.contains("Bekor qilish") | F.text.contains("Orqaga"), StateFilter("*"))
async def process_cancel(message: Message, state: FSMContext):
    """FSM holatlarini bekor qilish va asosiy menyuga qaytish"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())

@router.message(F.text.contains("Mening hisobim") | F.text.contains("Hisobim"), StateFilter("*"))
async def cmd_balance(message: Message, state: FSMContext):
    """Foydalanuvchi balansini ko'rsatish"""
    await state.clear()
    telegram_id = message.from_user.id
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            # Agar foydalanuvchi kutilmaganda bazada bo'lmasa, yaratamiz
            user, _ = await crud.get_or_create_user(
                db=db,
                telegram_id=telegram_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
        
        project_settings = await crud.get_project_settings(db)
        min_withdrawal = project_settings.min_withdrawal

    text = (
        f"👤 <b>Ism:</b> {html.escape(str(message.from_user.full_name))}\n"
        f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
        f"💳 <b>Hamyon balansi:</b> {user.balance} so'm\n"
        f"👥 <b>Taklif mukofoti:</b> {user.total_referrals} ta referal\n\n"
        f"📌 Minimal yechib olish summasi: {min_withdrawal} so'm\n\n"
        f"Quyidagi variantlardan birini tanlang 👇"
    )
    await message.answer(text, reply_markup=inline.get_withdrawal_keyboard(), parse_mode="HTML")

@router.message(F.text.contains("taklif qilish") | F.text.contains("Do'stlar"), StateFilter("*"))
async def cmd_referral(message: Message, state: FSMContext):
    """Foydalanuvchining shaxsiy referal havolasini chiqarish"""
    await state.clear()
    telegram_id = message.from_user.id
    
    # Bot username'ini olish
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    
    ref_link = f"https://t.me/{bot_username}?start={telegram_id}"
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            user, _ = await crud.get_or_create_user(
                db=db,
                telegram_id=telegram_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            
        project_settings = await crud.get_project_settings(db)
        referral_price = project_settings.referral_price

    text = (
        f"🎁 <b>Sizning referal havolangiz:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Taklif qilgan a'zolaringiz: <b>{user.total_referrals} ta</b>\n"
        f"💵 Do'stingiz muvaffaqiyatli ovoz bersa, balansingizga <b>{referral_price} so'm</b> qo'shiladi!"
    )
    await message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "withdraw_money")
async def process_withdraw_request(callback: CallbackQuery, state: FSMContext):
    """Pul yechish bosqichini boshlash"""
    telegram_id = callback.from_user.id
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        project_settings = await crud.get_project_settings(db)
        min_withdrawal = project_settings.min_withdrawal

    if not user or user.balance < min_withdrawal:
        await callback.answer(
            f"❌ Minimal yechish summasi {min_withdrawal} so'm. Balansingizda mablag' yetarli emas.",
            show_alert=True
        )
        return

    await callback.message.delete()
    await state.set_state(WithdrawStates.WAITING_FOR_CARD)
    await callback.message.answer(
        f"💳 Pul yechib olish so'rovini yuborish uchun plastik karta raqamingizni kiriting:\n"
        f"(Misol: 8600 1234 5678 9012)\n\n"
        f"Yechib olinadigan summa: <b>{user.balance} so'm</b>",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(WithdrawStates.WAITING_FOR_CARD)
async def process_card_input(message: Message, state: FSMContext):
    """Karta raqamini qabul qilish va pul yechish so'rovini yaratish"""
    card_number = message.text.strip()
    
    # Faqatgina raqamlarni ajratib olamiz
    clean_card = "".join(filter(str.isdigit, card_number))
    
    # Qat'iy tekshirish: karta raqami aniq 16 xonali bo'lishi kerak
    if len(clean_card) != 16:
        await message.answer("❌ Karta raqami noto'g'ri kiritildi. U aniq 16 xonali bo'lishi kerak. Qaytadan urinib ko'ring yoki bekor qiling:")
        return

    # Uzcard (8600, 5614, 5440, 6395) va Humo (9860, 4444) prefikslarini tekshirish
    valid_prefixes = ('8600', '9860', '4444', '5614', '5440', '6395')
    if not clean_card.startswith(valid_prefixes):
        await message.answer(
            "❌ Faqatgina Uzcard (8600, 5614, 5440, 6395) va Humo (9860, 4444) kartalari qabul qilinadi.\n"
            "Iltimos, boshqa karta raqam kiriting yoki bekor qiling:"
        )
        return

    telegram_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "Mavjud emas"
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            await state.clear()
            await message.answer("Tizimda xatolik yuz berdi. /start buyrug'ini bosing.", reply_markup=reply.get_user_menu())
            return
            
        amount_to_withdraw = user.balance
        
        # Withdraw yozuvini yaratish va user balansidan pul yechish
        try:
            withdrawal = await crud.create_withdrawal(
                db=db,
                telegram_id=telegram_id,
                amount=amount_to_withdraw,
                card_number=clean_card
            )
            total_referrals = user.total_referrals
        except ValueError as e:
            await state.clear()
            await message.answer(f"❌ Xatolik: {str(e)}", reply_markup=reply.get_user_menu())
            return

    await state.clear()
    await message.answer(
        f"✅ Pul yechish so'rovingiz qabul qilindi!\n\n"
        f"💰 Summa: <b>{amount_to_withdraw} so'm</b>\n"
        f"⏳ Mablag' <b>24 soat</b> ichida kartangizga o'tkazib beriladi.",
        reply_markup=reply.get_user_menu(),
        parse_mode="HTML"
    )

    # Karta raqamini maskalaymiz: faqat oxirgi 4 raqam ko'rinadi
    masked_card = f"{'*' * 4} {'*' * 4} {'*' * 4} {clean_card[-4:]}"

    # Adminlarga darhol bildirishnoma yuborish (maskalangan karta va HTML escape bilan)
    safe_username = html.escape(str(username))
    admin_message_text = (
        f"🚨 <b>Yangi pul yechish so'rovi!</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {safe_username} (ID: <code>{telegram_id}</code>)\n"
        f"👥 <b>Taklif qilgan odamlari soni:</b> <code>{total_referrals}</code> ta\n"
        f"💳 <b>Karta raqami:</b> <code>{masked_card}</code>\n"
        f"💰 <b>Summa:</b> <code>{amount_to_withdraw}</code> so'm"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=admin_message_text,
                reply_markup=inline.get_withdraw_action_keyboard(withdrawal.id),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Adminga (ID: {admin_id}) xabar yuborishda xatolik: {e}")

# --- Inline menyu callback query handlers ---

@router.callback_query(F.data == "menu_balance")
async def process_menu_balance(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan hisobni ko'rish"""
    await state.clear()
    telegram_id = callback.from_user.id
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            user, _ = await crud.get_or_create_user(
                db=db,
                telegram_id=telegram_id,
                username=callback.from_user.username,
                full_name=callback.from_user.full_name
            )
        
        project_settings = await crud.get_project_settings(db)
        min_withdrawal = project_settings.min_withdrawal

    text = (
        f"👤 <b>Ism:</b> {html.escape(str(callback.from_user.full_name))}\n"
        f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
        f"💳 <b>Hamyon balansi:</b> {user.balance} so'm\n"
        f"👥 <b>Taklif mukofoti:</b> {user.total_referrals} ta referal\n\n"
        f"📌 Minimal yechib olish summasi: {min_withdrawal} so'm\n\n"
        f"Quyidagi variantlardan birini tanlang 👇"
    )
    await callback.message.answer(text, reply_markup=inline.get_withdrawal_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_referral")
async def process_menu_referral(callback: CallbackQuery, state: FSMContext):
    """Inline menyudan referal havolani ko'rish"""
    await state.clear()
    telegram_id = callback.from_user.id
    
    bot_info = await callback.bot.get_me()
    bot_username = bot_info.username
    ref_link = f"https://t.me/{bot_username}?start={telegram_id}"
    
    async with async_session() as db:
        user = await crud.get_user(db, telegram_id)
        if not user:
            user, _ = await crud.get_or_create_user(
                db=db,
                telegram_id=telegram_id,
                username=callback.from_user.username,
                full_name=callback.from_user.full_name
            )
            
        project_settings = await crud.get_project_settings(db)
        referral_price = project_settings.referral_price

    text = (
        f"🎁 <b>Sizning referal havolangiz:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Taklif qilgan a'zolaringiz: <b>{user.total_referrals} ta</b>\n"
        f"💵 Do'stingiz muvaffaqiyatli ovoz bersa, balansingizga <b>{referral_price} so'm</b> qo'shiladi!"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@router.chat_join_request()
async def auto_approve_channel_join(event: ChatJoinRequest):
    """Kanal yoki guruhga qo'shilish so'rovi yuborilganda uni avtomatik tasdiqlaydi va botda foydalanuvchini ro'yxatdan o'tkazadi"""
    user_id = event.from_user.id
    username = event.from_user.username
    full_name = event.from_user.full_name

    try:
        # 1. So'rovni avtomatik tasdiqlaymiz (Kanalga kirishga ruxsat berish)
        await event.approve()

        # 2. Foydalanuvchi ma'lumotlar bazasida mavjud bo'lmasa yaratamiz
        async with async_session() as db:
            user_obj, created = await crud.get_or_create_user(
                db=db,
                telegram_id=user_id,
                username=username,
                full_name=full_name
            )
            project_settings = await crud.get_project_settings(db)
            referral_price = project_settings.referral_price

        # 3. Foydalanuvchiga shaxsiy xabar yuboramiz
        welcome_text = (
            f"🎉 <b>Tabriklaymiz! So'rovingiz tasdiqlandi.</b>\n\n"
            f"Siz kanalga muvaffaqiyatli qo'shildingiz. Men esa sizga loyihalarga ovoz berib "
            f"pul ishlashda yordam beradigan rasmiy botman.\n\n"
            f"💵 Har bir muvaffaqiyatli ovoz uchun referalingizga <b>{referral_price} so'm</b> taqdim etiladi.\n"
            f"Boshlash uchun quyidagi menyudan foydalaning 👇"
        )
        await event.bot.send_message(
            chat_id=user_id,
            text=welcome_text,
            reply_markup=reply.get_user_menu(),
            parse_mode="HTML"
        )
        logger.info(f"ChatJoinRequest muvaffaqiyatli bajarildi. Foydalanuvchi: {user_id} ({full_name})")

    except Exception as e:
        logger.error(f"ChatJoinRequest xatoligi: {e}", exc_info=True)

@router.message(F.text == "📢 To'lovlar kanali")
async def view_payouts_channel(message: Message):
    async with async_session() as db:
        project_settings = await crud.get_project_settings(db)
        payouts_channel = project_settings.payouts_channel or getattr(settings, "PAYOUTS_CHANNEL", "")
        
    if not payouts_channel:
        await message.answer("❌ To'lovlar kanali hozircha sozlanmagan.")
        return
        
    # URL ni tayyorlaymiz
    channel_username = payouts_channel
    if channel_username.startswith("@"):
        channel_username = channel_username[1:]
        
    channel_url = f"https://t.me/{channel_username}"
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Kanalga o'tish", url=channel_url)
    ]])
    
    await message.answer(
        f"📢 <b>To'lovlar kanalimiz:</b> {payouts_channel}\n\n"
        f"Foydalanuvchilarimizga to'lab berilgan barcha muvaffaqiyatli to'lovlar va cheklarni "
        f"ushbu kanal orqali shaffof tarzda kuzatib borishingiz mumkin! 👇",
        reply_markup=kb,
        parse_mode="HTML"
    )

