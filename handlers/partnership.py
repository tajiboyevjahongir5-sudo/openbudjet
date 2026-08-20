import logging
import random
import secrets
import hashlib
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from database.session import async_session
from database import crud
from keyboards import inline, reply
from config import settings
from database.models import ProjectSettings, Tariff

from aiogram.filters import StateFilter

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.text.contains("Hamkorlik"), StateFilter("*"))
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
    guide_path = os.path.join(BASE_DIR, "resources", "QOLLANMA.txt")
    
    try:
        # 1. Dastur kodi faylini yuboramiz
        input_file = FSInputFile(file_path, filename="open_budget_client_bot.py")
        await callback.message.answer_document(
            document=input_file,
            caption=(
                "💻 <b>Open Budget Mijoz Boti Kodi (v2.0 Premium)</b>\n\n"
                "Ushbu kod o'zining ichki asinxron ma'lumotlar bazasiga (aiosqlite + WAL) ega "
                "to'liq tayyor bot dasturidir."
            ),
            parse_mode="HTML"
        )
        
        # 2. O'rnatish va sozlash qo'llanmasini alohida fayl qilib yuboramiz
        if os.path.exists(guide_path):
            guide_file = FSInputFile(guide_path, filename="QOLLANMA.txt")
            await callback.message.answer_document(
                document=guide_file,
                caption=(
                    "📄 <b>Serverga o'rnatish va sozlash bo'yicha to'liq qo'llanma fayli!</b>\n\n"
                    "Ushbu faylda Railway, Ubuntu Linux (VPS) va Kompyuterda ishga tushirish qadamma-qadam tushuntirilgan."
                ),
                parse_mode="HTML",
                reply_markup=reply.get_user_menu()
            )
        
        # 3. Qisqa xulosa xabari
        instruction_text = (
            "✅ <b>Fayllar muvaffaqiyatli yuborildi!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ <code>open_budget_client_bot.py</code> — Botingizning to'liq dasturiy kodi.\n"
            "2️⃣ <code>QOLLANMA.txt</code> — Serverga o'rnatish bo'yicha batafsil qo'llanma.\n\n"
            "🔑 <b>Eslatma:</b> Botni ishga tushirganingizdan so'ng, unga kirib <b>/admin</b> buyrug'i orqali "
            "sotib olgan API kalitingizni va ovoz yig'ayotgan Loyiha ID raqamingizni kiritasiz."
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
            
        # Unikal to'lov summasini generatsiya qilamiz va xaridni yaratamiz (Concurrency safe)
        price = tariff.price
        purchase = None
        
        for _ in range(50):
            try:
                random_cents = random.randint(1, 999)
                unique_price = price + random_cents
                existing = await crud.get_pending_purchase_by_unique_price(db, unique_price)
                if existing:
                    continue
                purchase = await crud.create_pending_purchase(
                    db=db,
                    telegram_id=callback.from_user.id,
                    tariff_name=tariff.name,
                    price_uzs=price,
                    unique_price_uzs=unique_price,
                    votes_count=votes
                )
                break
            except Exception:
                await db.rollback()
                continue

        if not purchase:
            await callback.message.edit_text(
                "❌ To'lov fakturasini yaratishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
                reply_markup=inline.get_partnership_keyboard()
            )
            await callback.answer()
            return
        
    # Foydalanuvchiga to'lov ko'rsatmalari va qat'iy ogohlantirishni yuboramiz
    await callback.message.edit_text(
        f"💳 **API Kalit sotib olish uchun to'lov fakturasi:**\n\n"
        f"📦 Tarif: **{votes} ta Ovoz**\n"
        f"💰 Asl narxi: {price:,} UZS\n"
        f"💳 Karta raqami (Uzcard/Humo): `{card_number}`\n\n"
        f"💵 **O'TKAZISHINGIZ KERAK BO'LGAN SUMMA:**\n"
        f"👉 **`{unique_price:,} UZS`** 👈\n\n"
        f"⏱️ **To'lov muddati: 30 daqiqa!**\n"
        f"30 daqiqadan so'ng ushbu faktura avtomatik ravishda bekor qilinadi va noyob summa bandligi o'chiriladi.\n\n"
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
                
                # 1. Agar xaridda mavjud kalit ko'rsatilgan bo'lsa (Top-up), uning muddatini uzaytiramiz
                if purchase.generated_key:
                    plain_key = purchase.generated_key
                    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
                    api_key_obj = await crud.get_api_key_by_hash(db, key_hash)
                    if api_key_obj:
                        from datetime import datetime, timedelta
                        # Kalit muddatini uzaytirish
                        if api_key_obj.expires_at and api_key_obj.expires_at > datetime.utcnow():
                            api_key_obj.expires_at = api_key_obj.expires_at + timedelta(days=15)
                        else:
                            if api_key_obj.activated_at:
                                api_key_obj.expires_at = datetime.utcnow() + timedelta(days=15)
                            # agar hali faollashmagan bo'lsa, None holatida qoladi va birinchi so'rovda faollashadi
                        await db.commit()
                    else:
                        await crud.create_api_key(
                            db=db,
                            plain_key=plain_key,
                            owner_id=purchase.telegram_id,
                            initial_balance=500000
                        )
                    await crud.complete_purchase(db, purchase.id, generated_key=plain_key)
                else:
                    # Yangi API kalit yaratamiz
                    plain_key = f"ob_api_{secrets.token_hex(16)}"
                    await crud.create_api_key(
                        db=db,
                        plain_key=plain_key,
                        owner_id=purchase.telegram_id,
                        initial_balance=500000
                    )
                    await crud.complete_purchase(db, purchase.id, generated_key=plain_key)
                
                # 3. Agar xarid asosiy bot orqali amalga oshirilgan bo'lsa, asosiy botdan xabar yuboramiz
                # Agar mijoz botidan sotib olingan bo'lsa, kalitni mijoz boti o'zi avtomatik ulab oladi
                if getattr(purchase, "source", "MAIN_BOT") == "MAIN_BOT":
                    try:
                        await message.bot.send_message(
                            chat_id=purchase.telegram_id,
                            text=(
                                f"🎉 <b>To'lovingiz muvaffaqiyatli qabul qilindi!</b>\n\n"
                                f"📦 Tarif: <b>15 kunlik API Kalit (Cheksiz Ovoz)</b>\n"
                                f"💰 To'langan summa: <b>{purchase.unique_price_uzs:,} UZS</b>\n\n"
                                f"🔑 <b>Sizning API kalitingiz:</b>\n"
                                f"<code>{plain_key}</code>\n\n"
                                f"👉 Uni shifrlangan holatda API Dashboard orqali boshqarishingiz mumkin."
                            ),
                            parse_mode="HTML"
                        )
                        logger.info(f"Foydalanuvchiga API kalit asosiy botdan yuborildi: {purchase.telegram_id}")
                    except Exception as e:
                        logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")
                    
                # 4. Adminga xabar berish
                admin_ids = settings.ADMIN_IDS
                for admin_id in admin_ids:
                    try:
                        await message.bot.send_message(
                            chat_id=admin_id,
                            text=(
                                f"🔔 <b>Avtomatik Xarid Tasdiqlandi!</b>\n\n"
                                f"👤 Foydalanuvchi: <code>{purchase.telegram_id}</code>\n"
                                f"📦 Tarif: <b>{purchase.votes_count} ovoz</b>\n"
                                f"💵 Summa: <b>{purchase.unique_price_uzs:,} UZS</b>\n"
                                f"🔑 Kalit generatsiya qilinib yuborildi."
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                
                break  # Bitta xabar bitta xarid uchun!
