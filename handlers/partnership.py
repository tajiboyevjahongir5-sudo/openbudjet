import logging
import random
import secrets
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from database.session import async_session
from database import crud
from keyboards import inline, reply
from config import settings
from database.models import ProjectSettings, Tariff

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.text == "🤝 Hamkorlik")
async def cmd_partnership(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤝 **Hamkorlik bo'limiga xush kelibsiz!**\n\n"
        "Bu yerda siz o'zingizning shaxsiy Open Budget botingizni ishga tushirish uchun "
        "tayyor kodni yuklab olishingiz yoki u bilan ishlaydigan API kalitlarni sotib olishingiz mumkin.",
        reply_markup=inline.get_partnership_keyboard()
    )

@router.callback_query(F.data == "partnership_back")
async def process_partnership_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤝 **Hamkorlik bo'limiga xush kelibsiz!**\n\n"
        "Quyidagi xizmatlardan birini tanlang:",
        reply_markup=inline.get_partnership_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "partnership_get_code")
async def process_get_code(callback: CallbackQuery):
    await callback.message.delete()
    
    # resources dagi open_budget_client_bot.py faylini yuboramiz
    import os
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(BASE_DIR, "resources", "open_budget_client_bot.py")
    try:
        input_file = FSInputFile(file_path, filename="open_budget_client_bot.py")
        await callback.message.answer_document(
            document=input_file,
            caption=(
                "💻 **Open Budget Klient Bot kodi!**\n\n"
                "Ushbu kod siz o'z botingizni ochishingiz uchun soddalashtirilgan to'liq dasturiy koddir. "
                "Bot ishlashi uchun unga o'zingizning API kalitingizni (API_KEY) ulab qo'yishingiz shart."
            ),
            reply_markup=reply.get_user_menu()
        )
        
        instruction_text = (
            "⚙️ **Botni Serverga yuklash va sozlash bo'yicha to'liq qo'llanma:**\n\n"
            "Bot 24/7 rejimida uzluksiz ishlashi uchun uni serverga yuklashingiz kerak. Quyidagi qadamlarni bajaring:\n\n"
            "### 1️⃣ O'zgaruvchilarni (Variables) Sozlash\n"
            "Server sozlamalarida (yoki kod bilan birga `.env` faylida) quyidagi o'zgaruvchilarni (Environment Variables) aniq ko'rsatishingiz shart:\n\n"
            "🔹 <b>BOT_TOKEN</b>: @BotFather orqali yaratilgan botingizning maxfiy tokeni.\n"
            "🔹 <b>API_KEY</b>: Bizning botimizdan sotib olgan API kalitingiz (masalan: <code>ob_api_...</code>).\n"
            "🔹 <b>API_URL</b>: Bizning API wrapper serverimiz manzili (masalan: <code>https://sizning_botingiz.app/api/v1</code>).\n"
            "🔹 <b>PROJECT_ID</b>: Siz ovoz yig'ayotgan Open Budget tashabbusi (loyihasi) ID raqami.\n\n"
            "### 2️⃣ Kerakli kutubxonalarni o'rnatish\n"
            "Terminalda quyidagi buyruqni ishga tushiring:\n"
            "<code>pip install aiogram aiohttp</code>\n\n"
            "### 3️⃣ Serverga yuklash (Deploy):\n"
            "<b>Railway yoki Render orqali (Tavsiya etiladi - oson):</b>\n"
            "1. GitHub'da yangi yopiq (Private) repo ochib, ushbu kodni yuklang.\n"
            "2. Railway.app saytiga kirib GitHub repongizni ulang.\n"
            "3. Settings (Variables) bo'limida yuqoridagi 4 ta o'zgaruvchini kiriting.\n"
            "4. Bot avtomatik ishga tushadi!\n\n"
            "<b>Ubuntu/Linux (VPS) orqali:</b>\n"
            "1. Faylni serverga yuklang.\n"
            "2. <code>.env</code> faylini yarating.\n"
            "3. Fondagi rejimda ishga tushirish uchun quyidagi buyruqni yozing:\n"
            "   <code>nohup python open_budget_client_bot.py &</code>"
        )
        await callback.message.answer(instruction_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Fayl yuborishda xato: {e}")
        await callback.message.answer(
            "❌ Afsuski, hozirda bot kodini yuklab bo'lmaydi. Iltimos, keyinroq urinib ko'ring.",
            reply_markup=reply.get_user_menu()
        )
    await callback.answer()

@router.callback_query(F.data == "partnership_buy_api")
async def process_buy_api(callback: CallbackQuery):
    async with async_session() as db:
        tariffs = await crud.get_all_tariffs(db)
        
    await callback.message.edit_text(
        "🔑 **API Kalit sotib olish uchun tarifni tanlang:**\n\n"
        "Tarif summasiga avtomatik to'lovni tekshirish uchun 1 so'mdan 99 so'mgacha bo'lgan "
        "kichik summa qo'shib beriladi (Masalan: 150 043 UZS). Aynan o'sha summani to'lashingiz shart!",
        reply_markup=inline.get_tariffs_keyboard(tariffs)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("buy_tariff_"))
async def process_select_tariff(callback: CallbackQuery):
    votes = int(callback.data.split("_")[-1])
    
    async with async_session() as db:
        tariff = await crud.get_tariff_by_votes(db, votes)
        if not tariff:
            await callback.answer("Noto'g'ri tarif tanlandi.", show_alert=True)
            return
            
        settings_db = await crud.get_project_settings(db)
        card_number = settings_db.card_number
        
        if not card_number:
            await callback.message.edit_text(
                "❌ Hozircha to'lov qabul qilish kartasi sozlanmagan. "
                "Iltimos, keyinroq qayta urining yoki administratorga xabar bering.",
                reply_markup=inline.get_partnership_keyboard()
            )
            await callback.answer()
            return
            
        # Unikal to'lov summasini generatsiya qilamiz
        price = tariff.price
        max_attempts = 100
        unique_price = price
        
        for _ in range(max_attempts):
            random_cents = random.randint(1, 99)
            test_price = price + random_cents
            # Tekshiramiz, bazada bu summa bilan PENDING to'lov bormi
            existing = await crud.get_pending_purchase_by_unique_price(db, test_price)
            if not existing:
                unique_price = test_price
                break
                
        # To'lov yozuvini yaratamiz
        purchase = await crud.create_pending_purchase(
            db=db,
            telegram_id=callback.from_user.id,
            tariff_name=tariff.name,
            price_uzs=price,
            unique_price_uzs=unique_price,
            votes_count=votes
        )
        
    # Foydalanuvchiga to'lov ko'rsatmalari va qat'iy ogohlantirishni yuboramiz
    await callback.message.edit_text(
        f"💳 **API Kalit sotib olish uchun to'lov fakturasi:**\n\n"
        f"📦 Tarif: **{votes} ta Ovoz**\n"
        f"💰 Asl narxi: {price:,} UZS\n"
        f"💳 Karta raqami (Uzcard/Humo): `{card_number}`\n\n"
        f"💵 **O'TKAZISHINGIZ KERAK BO'LGAN SUMMA:**\n"
        f"👉 **`{unique_price:,} UZS`** 👈\n\n"
        f"⚠️ **QAT'IY TALAB (DIQQAT):**\n"
        f"Karta hisobiga aynan **`{unique_price:,} UZS`** o'tkazishingiz shart (tiyinlarigacha aniq!). "
        f"Agar 1 so'm bo'lsa ham boshqa summa o'tkazsangiz (masalan, 150,000 UZS), bot to'lovni "
        f"**avtomatik aniqlay olmaydi** va sizga kalit taqdim etilmaydi!",
        reply_markup=inline.get_payment_keyboard(purchase.id),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("payment_paid_"))
async def process_payment_paid(callback: CallbackQuery):
    purchase_id = int(callback.data.split("_")[-1])
    
    await callback.message.answer(
        "🔄 **To'lovingiz tekshirilmoqda...**\n\n"
        "O'tkazma kartaga tushishi bilan tizim sizga API kalitni avtomatik tarzda shu yerga yuboradi. "
        "Bu odatda 1-3 daqiqa vaqt oladi. Iltimos, kutib turing va yangi xabar kelishini kuzating."
    )
    await callback.answer()

@router.callback_query(F.data.startswith("payment_cancel_"))
async def process_payment_cancel(callback: CallbackQuery):
    purchase_id = int(callback.data.split("_")[-1])
    
    async with async_session() as db:
        purchase = await db.get(crud.APIKeyPurchase, purchase_id)
        if purchase and purchase.status == "PENDING":
            purchase.status = "CANCELLED"
            await db.commit()
            
    await callback.message.edit_text(
        "❌ To'lov so'rovi bekor qilindi.",
        reply_markup=inline.get_partnership_keyboard()
    )
    await callback.answer()

# --- To'lov kanalini kuzatish (Avtomatik to'lovni tasdiqlash) ---

@router.channel_post()
async def process_payment_notification(message: Message):
    """
    To'lov xabarnomalari keladigan kanalni kuzatib boradi.
    Kelgan bank bildirishnomasidan summani aniqlab, mos keluvchi PENDING xaridni topadi.
    """
    text = message.text or message.caption or ""
    if not text:
        return
        
    logger.info(f"Yangi to'lov xabarnomasi keldi: {text[:100]}...")
    
    async with async_session() as db:
        settings_db = await crud.get_project_settings(db)
        
        # Agar kanal ID to'g'ri kelmasa yoki to'lov kanali sozlanmagan bo'lsa, o'tkazib yuboramiz
        # Telegram Channel ID'lari odatda -100 bilan boshlanadi, shuning uchun solishtiramiz
        if not settings_db.payment_channel_id or message.chat.id != settings_db.payment_channel_id:
            return
            
        # Matn ichidan barcha sonlarni ajratib olamiz (komma va probellarni olib tashlaymiz)
        # Masalan: "150 043", "150,043", "150043.00" -> "150043"
        cleaned_text = re.sub(r'\s+|,', '', text)
        numbers = [int(n) for n in re.findall(r'\d+', cleaned_text) if n.isdigit()]
        
        # Kutilayotgan barcha to'lovlarni yuklaymiz
        pending_purchases = await crud.get_all_pending_purchases(db)
        
        for purchase in pending_purchases:
            # Agar xabardagi biron bir raqam unikal summaga mos kelsa
            if purchase.unique_price_uzs in numbers:
                logger.info(f"Mos keluvchi to'lov topildi: {purchase.unique_price_uzs} UZS. Xarid ID: {purchase.id}")
                
                # 1. Xaridni yakunlaymiz (COMPLETED)
                await crud.complete_purchase(db, purchase.id)
                
                # 2. Yangi API kalit yaratamiz
                plain_key = f"ob_api_{secrets.token_hex(16)}"
                await crud.create_api_key(
                    db=db,
                    plain_key=plain_key,
                    owner_id=purchase.telegram_id,
                    initial_balance=purchase.votes_count * 1500 # 1 ovoz = 1500 so'm
                )
                
                # 3. Foydalanuvchiga API kalitni yuboramiz
                try:
                    await message.bot.send_message(
                        chat_id=purchase.telegram_id,
                        text=(
                            f"🎉 **To'lovingiz muvaffaqiyatli qabul qilindi!**\n\n"
                            f"📦 Tarif: **{purchase.votes_count} ta Ovoz**\n"
                            f"💰 To'langan summa: {purchase.unique_price_uzs:,} UZS\n\n"
                            f"🔑 **Sizning API kalitingiz:**\n"
                            f"`{plain_key}`\n\n"
                            f"👉 Uni shifrlangan holatda [API Dashboard](t.me) orqali boshqarishingiz mumkin.\n"
                            f"💡 Ishlatish bo'yicha [API Sotib Oluvchi Mijozlar uchun Sodda Qo'llanma](file:///C:/Users/user/.gemini/antigravity/brain/bcb65563-bfb8-4cde-a3a5-ee497b4bc0a6/buyer_guide.md)ni o'qing."
                        ),
                        parse_mode="HTML"
                    )
                    logger.info(f"Foydalanuvchiga API kalit yuborildi: {purchase.telegram_id}")
                except Exception as e:
                    logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")
                    
                # 4. Adminga xabar berish
                admin_ids = settings_db.ADMIN_IDS
                for admin_id in admin_ids:
                    try:
                        await message.bot.send_message(
                            chat_id=admin_id,
                            text=(
                                f"🔔 **Avtomatik Xarid Tasdiqlandi!**\n\n"
                                f"👤 Foydalanuvchi: `{purchase.telegram_id}`\n"
                                f"📦 Tarif: {purchase.votes_count} ovoz\n"
                                f"💵 Summa: {purchase.unique_price_uzs:,} UZS\n"
                                f"🔑 Kalit generatsiya qilinib yuborildi."
                            )
                        )
                    except Exception:
                        pass
                
                break  # Bitta xabar bitta xarid uchun!
