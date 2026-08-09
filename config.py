import os
from typing import List
from urllib.parse import urlparse
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram Bot Token
    BOT_TOKEN: str

    # Webhook autentifikatsiyasi uchun maxfiy kalit
    WEBHOOK_SECRET_TOKEN: str = "default_secret_token_ob_bot_998"

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

    # Cloudflare Workers reverse proxy URL (proksisiz bepul IP rotatsiya qilish uchun)
    CLOUDFLARE_PROXY_URL: str = ""

    # Standart HTTP/SOCKS5 turar-joy proksi ulanishi (masalan: http://user:pass@ip:port)
    PROXY_URL: str = ""

    # FastAPI Server parametrlari
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    @field_validator("CLOUDFLARE_PROXY_URL")
    @classmethod
    def validate_proxy_url(cls, v: str) -> str:
        if not v:
            return v
        parsed = urlparse(v)
        if parsed.scheme != "https":
            raise ValueError("Proxy URL must use HTTPS scheme")
        host = parsed.netloc.lower()
        is_trusted = (
            host == "openbudget.uz" or 
            host.endswith(".workers.dev") or 
            host.endswith(".pages.dev") or 
            host.startswith("localhost") or 
            host.startswith("127.0.0.1")
        )
        if not is_trusted:
            raise ValueError("Proxy URL host is not in the trusted allowlist (openbudget.uz, workers.dev, pages.dev)")
        if parsed.username or parsed.password:
            raise ValueError("Proxy URL cannot contain credentials")
        return v

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
