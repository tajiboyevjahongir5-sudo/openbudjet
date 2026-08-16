import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Base
from database.session import engine, get_db
from database import crud
from database.session import async_session
from database.fsm_storage import PostgresStorage
from handlers import user, vote, admin, partnership
from middlewares import ThrottlingMiddleware
from api.v1 import router as api_router

async def require_admin(
    init_data: str = None,
    admin_token: str = None,
    admin_token_header: str = Header(None, alias="admin-token"),
    tg_init_data: str = Header(None, alias="tg-init-data"),
) -> int:
    """FastAPI Dependency: Admin autentifikatsiyasini tekshiradi va telegram_id qaytaradi"""
    from utils.api_auth import verify_telegram_init_data_detailed, verify_admin_token, is_admin_user
    
    user_data = None
    raw_data = init_data or tg_init_data
    if raw_data:
        user_data, _ = verify_telegram_init_data_detailed(raw_data)
    
    telegram_id = None
    if user_data:
        telegram_id = user_data.get("id", 0)
    elif admin_token or admin_token_header:
        telegram_id = verify_admin_token(admin_token or admin_token_header)
    
    if not telegram_id or not is_admin_user(telegram_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Kirish taqiqlangan! Faqat adminlar kirishi mumkin."}
        )
    return telegram_id

# Logger sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Bot va Dispatcher obyektlarini yaratish
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=PostgresStorage())

# Routerlarni ulash
dp.include_router(user.router)
dp.include_router(vote.router)
dp.include_router(admin.router)
dp.include_router(partnership.router)


# Anti-flood (Throttling) middleware ulash
dp.message.middleware(ThrottlingMiddleware(time_limit=1.0))
dp.callback_query.middleware(ThrottlingMiddleware(time_limit=1.0))

# HTML shablonlari uchun Jinja2Templates sozlash (mutlaq yo'l orqali)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

async def init_db():
    """Ma'lumotlar bazasi jadvallarini yaratish va birlamchi sozlamalarni kiritish"""
    logger.info("Ma'lumotlar bazasi jadvallari tekshirilmoqda/yaratilmoqda...")
    
    # 1. Barcha jadvallarni metadata orqali yaratamiz
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Alohida tranzaksiyalarda ustunlarni ALTER qilamiz (bittasi xato qilsa ham qolganlari ishlaydi)
    async with engine.connect() as conn:
        for sql in [
            "ALTER TABLE project_settings ADD COLUMN voter_reward FLOAT DEFAULT 0.0;",
            "ALTER TABLE project_settings ADD COLUMN channel_username VARCHAR(100);",
            "ALTER TABLE project_settings ADD COLUMN card_number VARCHAR(30);",
            "ALTER TABLE project_settings ADD COLUMN payment_channel_id BIGINT;",
            "ALTER TABLE api_key_purchases ADD COLUMN source VARCHAR(20) DEFAULT 'MAIN_BOT';",
            "ALTER TABLE api_key_purchases ADD COLUMN generated_key VARCHAR(500);",
        ]:
            try:
                await conn.execute(text(sql))
                await conn.commit()
            except Exception:
                # Agar ustun allaqachon mavjud bo'lsa yoki boshqa xato bo'lsa, commitni bekor qilamiz va davom etamiz
                await conn.rollback()

        # PostgreSQL uchun maxsus qo'shimcha FSM jadvali
        if "postgresql" in engine.url.drivername:
            try:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS fsm_states (
                        key VARCHAR(255) PRIMARY KEY,
                        state VARCHAR(255),
                        data TEXT DEFAULT '{}'
                    );
                """))
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                logger.warning(f"PostgreSQL FSM jadvali yaratishda xato: {e}")
                
    logger.info("Avtomatik migratsiyalar bajarildi.")
    
    async with async_session() as db:
        await crud.get_project_settings(db)
        await crud.seed_default_tariffs(db)
    logger.info("Ma'lumotlar bazasi tayyor!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Baza va Botni ishga tushirish
    await init_db()
    
    if settings.WEBHOOK_URL:
        # Webhook rejimi
        webhook_url = f"{settings.WEBHOOK_URL.rstrip('/')}/webhook"
        logger.info(f"Webhook o'rnatilmoqda: {webhook_url}")
        from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
        try:
            await bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True,
                secret_token=settings.WEBHOOK_SECRET_TOKEN,
                request_timeout=30
            )
            logger.info("Webhook muvaffaqiyatli o'rnatildi.")
        except TelegramRetryAfter as e:
            logger.warning(f"Telegram Flood Control: {e.retry_after} soniya kutilmoqda...")
            await asyncio.sleep(e.retry_after)
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    drop_pending_updates=True,
                    secret_token=settings.WEBHOOK_SECRET_TOKEN
                )
                logger.info("Webhook qayta urinishda muvaffaqiyatli o'rnatildi.")
            except Exception as e_retry:
                logger.error(f"Webhook qayta urinishda o'rnatilmadi: {e_retry}")
        except TelegramNetworkError as e:
            logger.error(f"Telegram tarmoq xatoligi (set_webhook): {e}")
        except Exception as e:
            logger.error(f"Webhook o'rnatishda kutilmagan xatolik: {e}")

    else:
        # Polling rejimi (FastAPI ichida background task sifatida)
        logger.info("WEBHOOK_URL topilmadi. Bot polling rejimida ishga tushmoqda...")
        await bot.delete_webhook(drop_pending_updates=True)
        polling_task = asyncio.create_task(dp.start_polling(bot))
        app.state.polling_task = polling_task

    yield

    # Shutdown: Bot va ulanishlarni yopish
    logger.info("Ilova to'xtatilmoqda...")
    if not settings.WEBHOOK_URL and hasattr(app.state, "polling_task"):
        app.state.polling_task.cancel()
        try:
            await app.state.polling_task
        except asyncio.CancelledError:
            pass
    
    await bot.session.close()
    await engine.dispose()
    logger.info("Ilova to'liq to'xtatildi.")

# FastAPI ilovasi
app = FastAPI(
    title="Open Budget Telegram Bot API",
    version="1.0.0",
    lifespan=lifespan
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Server ichki xatosi yuz berdi."},
    )

# FastAPI endpointlari
app.include_router(api_router, prefix="/api/v1", tags=["Commercial API"])

class CreateKeySchema(BaseModel):
    owner_id: int | None = None
    balance_uzs: int = 150000

class TopUpSchema(BaseModel):
    amount: int

class ToggleSchema(BaseModel):
    is_active: bool

@app.get("/")
async def health_check():
    return {"status": "running", "mode": "webhook" if settings.WEBHOOK_URL else "polling"}

@app.get("/admin/api-dashboard", response_class=HTMLResponse)
async def get_admin_dashboard(request: Request):
    """Admin API boshqaruv paneli sahifasini qaytaradi"""
    return templates.TemplateResponse("api_dashboard.html", {"request": request})

@app.get("/admin/api/keys")
async def get_keys_api(
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):


    keys = await crud.get_all_api_keys(db)
    total_balance = sum(k.balance_uzs for k in keys)
    
    # Jami muvaffaqiyatli ovozlar soni
    from database.models import VotesHistory, VoteStatus
    v_result = await db.execute(select(func.count(VotesHistory.id)).where(VotesHistory.status == VoteStatus.SUCCESS))
    total_votes = v_result.scalar_one() or 0

    from utils.encrypt import decrypt_key
    serialized_keys = []
    for k in keys:
        try:
            plain = decrypt_key(k.key)
            masked = f"{plain[:11]}...{plain[-4:]}"
        except Exception:
            plain = ""
            masked = "xatolik..."
            
        serialized_keys.append({
            "id": k.id,
            "key_masked": masked,
            "raw_key_decrypted": plain, # Faqattgina dashboardda nusxalash uchun uzatamiz
            "owner_id": k.owner_id,
            "balance_uzs": k.balance_uzs,
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat()
        })
        
    tariffs = await crud.get_all_tariffs(db)
    serialized_tariffs = [{
        "id": t.id,
        "votes": t.votes,
        "name": t.name,
        "price": t.price
    } for t in tariffs]
    
    settings_db = await crud.get_project_settings(db)
    serialized_settings = {
        "card_number": settings_db.card_number or "",
        "payment_channel_id": settings_db.payment_channel_id or ""
    }
    
    return {
        "status": "success",
        "stats": {
            "total_keys": len(keys),
            "total_balance": total_balance,
            "total_votes": total_votes
        },
        "keys": serialized_keys,
        "tariffs": serialized_tariffs,
        "settings": serialized_settings
    }

@app.post("/admin/api/keys")
async def create_key_api(
    req: CreateKeySchema,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):

    import secrets
    plain_key = f"ob_api_{secrets.token_hex(16)}"
    
    await crud.create_api_key(
        db=db,
        plain_key=plain_key,
        owner_id=req.owner_id,
        initial_balance=req.balance_uzs
    )
    
    return {
        "status": "success",
        "key_plain": plain_key
    }

@app.post("/admin/api/keys/{key_id}/topup")
async def topup_key_api(
    key_id: int,
    req: TopUpSchema,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):

    updated = await crud.update_api_key_balance(db, key_id, req.amount)
    if not updated:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, 
            content={"status": "error", "message": "API kalit topilmadi."}
        )
        
    return {"status": "success"}

@app.post("/admin/api/keys/{key_id}/toggle")
async def toggle_key_api(
    key_id: int,
    req: ToggleSchema,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):

    updated = await crud.toggle_api_key_status(db, key_id, req.is_active)
    if not updated:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, 
            content={"status": "error", "message": "API kalit topilmadi."}
        )
        
    return {"status": "success"}

@app.delete("/admin/api/keys/{key_id}")
async def delete_key_api(
    key_id: int,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):

    success = await crud.delete_api_key(db, key_id)
    if not success:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, 
            content={"status": "error", "message": "API kalit topilmadi."}
        )
        
    return {"status": "success"}

@app.get("/captcha", response_class=HTMLResponse)
async def get_captcha_page(request: Request, session_id: str = "default", sign: str = ""):
    """Foydalanuvchilar captchani yechishi uchun chiroyli HTML sahifasi"""
    if session_id != "default":
        from utils.security import verify_session_signature
        if not verify_session_signature(session_id, sign, settings.BOT_TOKEN):
            return HTMLResponse("<h1>403 Forbidden: Noto'g'ri yoki yo'q imzo</h1>", status_code=403)

    captcha_image = None
    captcha_key = None
    try:
        user_id = int(session_id)
        from aiogram.fsm.storage.base import StorageKey
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        state_data = await dp.storage.get_data(key)
        captcha_image = state_data.get("captcha_image")
        captcha_key = state_data.get("captcha_key")
    except Exception as e:
        logger.warning(f"FSM dan captcha olishda xatolik: {e}")

    return templates.TemplateResponse(
        "captcha.html", 
        {
            "request": request, 
            "session_id": session_id,
            "captcha_image": captcha_image,
            "captcha_key": captcha_key
        }
    )

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Telegram webhook orqali keladigan yangilanishlarni qabul qilish"""
    if not settings.WEBHOOK_URL:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "Webhook rejimi faol emas."}
        )
    
    # 1. Secret token autentifikatsiyasi
    received_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if received_token != settings.WEBHOOK_SECRET_TOKEN:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Ruxsatsiz so'rov."}
        )

    # 2. JSON yuklama hajmini tekshirish (10MB limit)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 10 * 1024 * 1024:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"message": "Payload juda katta."}
        )

    try:
        update_data = await request.json()
        update = Update.model_validate(update_data)
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook xatoligi: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Xatolik yuz berdi"}
        )

class UpdateTariffSchema(BaseModel):
    votes: int
    price: int

@app.post("/admin/api/tariffs/{tariff_id}")
async def update_tariff_api(
    tariff_id: int,
    schema: UpdateTariffSchema,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
        
    # Boshqa tarifda bu ovozlar soni ishlatilmaganligini tekshiramiz
    from database.models import Tariff
    existing = await db.execute(
        select(Tariff).where(Tariff.votes == schema.votes, Tariff.id != tariff_id)
    )
    if existing.scalar_one_or_none():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "message": f"{schema.votes} ovozli boshqa tarif allaqachon mavjud!"}
        )
        
    result = await db.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"status": "error", "message": "Tarif topilmadi."}
        )
        
    tariff.votes = schema.votes
    tariff.name = f"{schema.votes} Ovoz"
    tariff.price = schema.price
    
    await db.commit()
    await db.refresh(tariff)
        
    return {"status": "success", "message": "Tarif muvaffaqiyatli yangilandi!"}


class UpdateSettingsSchema(BaseModel):
    card_number: str | None = None
    payment_channel_id: str | None = None

@app.post("/admin/api/settings")
async def update_settings_api(
    schema: UpdateSettingsSchema,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
        
    card_number_val = schema.card_number.strip() if schema.card_number else None
    if not card_number_val:
        card_number_val = None

    channel_id = None
    if schema.payment_channel_id:
        val = str(schema.payment_channel_id).strip()
        if val:
            try:
                channel_id = int(val)
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"status": "error", "message": "To'lov kanali IDsi faqat butun son bo'lishi shart (masalan: -1001234567890)!"}
                )
                
    settings_db = await crud.get_project_settings(db)
    settings_db.card_number = card_number_val
    settings_db.payment_channel_id = channel_id
    await db.commit()
    await db.refresh(settings_db)
        
    return {"status": "success", "message": "Tizim sozlamalari muvaffaqiyatli saqlandi!"}


# FastAPI ilovasini ishga tushirish
if __name__ == "__main__":
    import uvicorn
    # Double-import va Router xatoligini oldini olish uchun reload=False qilinadi
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
