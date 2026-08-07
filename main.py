import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

from config import settings
from database.models import Base
from database.session import engine
from database import crud
from database.session import async_session
from handlers import user, vote, admin

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
dp = Dispatcher(storage=MemoryStorage())

# Routerlarni ulash
dp.include_router(user.router)
dp.include_router(vote.router)
dp.include_router(admin.router)

# HTML shablonlari uchun Jinja2Templates sozlash
templates = Jinja2Templates(directory="templates")

async def init_db():
    """Ma'lumotlar bazasi jadvallarini yaratish va birlamchi sozlamalarni kiritish"""
    logger.info("Ma'lumotlar bazasi jadvallari tekshirilmoqda/yaratilmoqda...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
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
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
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
    """Foydalanuvchilar puzlli captchani yechishi uchun chiroyli HTML sahifasi"""
    return templates.TemplateResponse(
        "captcha.html", 
        {"request": request, "session_id": session_id}
    )

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Telegram webhook orqali keladigan yangilanishlarni qabul qilish"""
    if not settings.WEBHOOK_URL:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "Webhook rejimi faol emas."}
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
