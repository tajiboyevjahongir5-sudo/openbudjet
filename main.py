import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update
from sqlalchemy import text

from config import settings
from database.models import Base
from database.session import engine
from database import crud
from database.session import async_session
from database.fsm_storage import PostgresStorage
from handlers import user, vote, admin
from middlewares import ThrottlingMiddleware

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

# Anti-flood (Throttling) middleware ulash
dp.message.middleware(ThrottlingMiddleware(time_limit=1.0))
dp.callback_query.middleware(ThrottlingMiddleware(time_limit=1.0))

# HTML shablonlari uchun Jinja2Templates sozlash (mutlaq yo'l orqali)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

async def init_db():
    """Ma'lumotlar bazasi jadvallarini yaratish va birlamchi sozlamalarni kiritish"""
    logger.info("Ma'lumotlar bazasi jadvallari tekshirilmoqda/yaratilmoqda...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Barcha ma'lumotlar bazalari uchun avtomatik ustun qo'shish (SQLite / PostgreSQL)
        # SQLite da 'IF NOT EXISTS' ishlamasligi mumkin, shuning uchun 'try-except' bilan bajaramiz.
        for sql in [
            "ALTER TABLE project_settings ADD COLUMN voter_reward FLOAT DEFAULT 0.0;",
            "ALTER TABLE project_settings ADD COLUMN channel_username VARCHAR(100);",
        ]:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # Ustun allaqachon mavjud bo'lsa xatolik yuz beradi, uni tashlab o'tamiz

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
            except Exception as e:
                logger.warning(f"PostgreSQL FSM jadvali yaratishda xato: {e}")
        logger.info("Avtomatik migratsiyalar bajarildi.")

    
    async with async_session() as db:
        await crud.get_project_settings(db)
    logger.info("Ma'lumotlar bazasi tayyor!")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Baza va Botni ishga tushirish
    await init_db()
    
    if settings.WEBHOOK_URL:
        # Webhook rejimi
        webhook_url = f"{settings.WEBHOOK_URL}/webhook"
        logger.info(f"Webhook o'rnatilmoqda: {webhook_url}")
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            secret_token=settings.WEBHOOK_SECRET_TOKEN
        )
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

# FastAPI endpointlari
@app.get("/")
async def health_check():
    return {"status": "running", "mode": "webhook" if settings.WEBHOOK_URL else "polling"}

@app.get("/captcha", response_class=HTMLResponse)
async def get_captcha_page(request: Request, session_id: str = "default"):
    """Foydalanuvchilar captchani yechishi uchun chiroyli HTML sahifasi"""
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

# FastAPI ilovasini ishga tushirish
if __name__ == "__main__":
    import uvicorn
    # Double-import va Router xatoligini oldini olish uchun reload=False qilinadi
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
