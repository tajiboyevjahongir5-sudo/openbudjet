import os
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram Bot Token
    BOT_TOKEN: str

    # Database URL: default sifatida SQLite ga ulanadi, PostgreSQL ga oson almashtirish mumkin
    DATABASE_URL: str = "sqlite+aiosqlite:///database.db"

    # Admin Telegram ID-lari (vergul bilan ajratilgan matn: "1234567,7654321")
    ADMIN_IDS_RAW: str = ""

    # Webhook URL (FastAPI webhook uchun)
    WEBHOOK_URL: str = ""

    # Web App URL (Telegram Mini App ochiladigan asosiy manzil. Bo'sh bo'lsa localhost ishlatiladi)
    WEB_APP_URL: str = ""

    # Open Budget Mock rejimi (test qilish uchun True, real sayt bilan ishlashda False qilinadi)
    MOCK_OPENBUDGET: bool = True

    # FastAPI Server parametrlari
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def ADMIN_IDS(self) -> List[int]:
        if not self.ADMIN_IDS_RAW:
            return []
        try:
            return [int(x.strip()) for x in self.ADMIN_IDS_RAW.split(",") if x.strip()]
        except ValueError:
            return []

settings = Settings()
