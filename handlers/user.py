import re
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from config import settings
from database.session import async_session
from database import crud
from states.user_states import WithdrawStates
from keyboards import reply, inline

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("start"))
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
            f"👋 Assalomu alaykum, {message.from_user.full_name}!\n\n"
            f"Ovoz yig'ish botimizga xush kelibsiz.\n"
            f"Siz bu yerda o'z ovozingizni berib va do'stlaringizni taklif qilib pul ishlashingiz mumkin.\n\n"
            f"💵 Har bir muvaffaqiyatli ovoz uchun taklif qiluvchiga **{referral_price} so'm** to'lanadi.\n"
            f"Boshlash uchun quyidagi menyudan foydalaning 👇"
        )
    else:
        welcome_text = (
            f"👋 Assalomu alaykum, {message.from_user.full_name}!\n"
            f"Qayta tashrifingizdan xursandmiz. Quyidagi menyudan foydalaning 👇"
        )

    await message.answer(welcome_text, reply_markup=reply.get_user_menu())

@router.message(F.text == "❌ Jarayonni bekor qilish")
async def process_cancel(message: Message, state: FSMContext):
    """FSM holatlarini bekor qilish va asosiy menyuga qaytish"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await message.answer("Jarayon bekor qilindi.", reply_markup=reply.get_user_menu())

@router.message(F.text == "💎 Mening hisobim")
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
        f"💰 **Sizning balansingiz:**\n\n"
        f"💵 Mablag': {user.balance} so'm\n"
        f"👥 Taklif qilgan odamlaringiz: {user.total_referrals} ta\n\n"
        f"📌 Minimal yechib olish summasi: {min_withdrawal} so'm"
    )
    await message.answer(text, reply_markup=inline.get_withdrawal_keyboard())

@router.message(F.text == "📣 Do'stlarni taklif qilish")
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
        f"🔗 **Sizning referal havolangiz:**\n"
        f"`{ref_link}`\n\n"
        f"👥 Muvaffaqiyatli taklif qilganlar soni: {user.total_referrals} ta\n"
        f"💵 Har bir taklif qilgan odamingiz muvaffaqiyatli ovoz bersa, balansizga **{referral_price} so'm** qo'shiladi!"
    )
    # MarkdownV2 yoki HTML ga mos ravishda formatlash
    await message.answer(text, parse_mode="Markdown")

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
        f"Yechib olinadigan summa: **{user.balance} so'm**",
        reply_markup=reply.get_cancel_keyboard(),
        parse_mode="Markdown"
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
        f"💰 Summa: **{amount_to_withdraw} so'm**\n"
        f"⏳ Mablag' **24 soat** ichida kartangizga o'tkazib beriladi.",
        reply_markup=reply.get_user_menu(),
        parse_mode="Markdown"
    )

    # Adminlarga darhol bildirishnoma yuborish
    admin_message_text = (
        f"🚨 **Yangi pul yechish so'rovi!**\n\n"
        f"👤 **Foydalanuvchi:** {username} (ID: `{telegram_id}`)\n"
        f"👥 **Taklif qilgan odamlari soni:** `{total_referrals}` ta\n"
        f"💳 **Karta raqami:** `{card_number}`\n"
        f"💰 **Summa:** `{amount_to_withdraw}` so'm"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=admin_message_text,
                reply_markup=inline.get_withdraw_action_keyboard(withdrawal.id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Adminga (ID: {admin_id}) xabar yuborishda xatolik: {e}")
