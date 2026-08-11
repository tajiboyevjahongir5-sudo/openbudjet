import hashlib
import hmac
import json
import urllib.parse
import logging
from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from database.session import get_db
from database import crud
from database.models import APIKey
from config import settings

logger = logging.getLogger(__name__)

def verify_telegram_init_data(init_data: str) -> dict | None:
    """
    Telegram WebApp initData imzosini bot tokeni orqali tekshiradi.
    Muvaffaqiyatli bo'lsa, user ma'lumotlarini (dict) qaytaradi, aks holda None.
    """
    if not init_data:
        logger.warning("verify_telegram_init_data: init_data is empty.")
        return None
        
    try:
        # Decode first if double URL encoded
        if "%3D" in init_data or "%26" in init_data:
            init_data = urllib.parse.unquote(init_data)
            
        parsed = dict(urllib.parse.parse_qsl(init_data))
        if "hash" not in parsed:
            logger.warning(f"verify_telegram_init_data: 'hash' not found in parsed data: {list(parsed.keys())}")
            return None
            
        tg_hash = parsed.pop("hash")
        
        # Qolgan parametrlarni alifbo tartibida saralaymiz
        sorted_params = sorted(parsed.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_params)
        
        # Secret key yaratamiz (HMAC-SHA256 "WebappData" bilan)
        secret_key = hmac.new(b"WebappData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        
        # InitData imzosini hisoblaymiz
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != tg_hash:
            logger.warning(f"verify_telegram_init_data: Signature mismatch! Calculated: {calculated_hash}, Received: {tg_hash}")
            return None
            
        # User ma'lumotlarini JSON qilib o'qiymiz
        user_str = parsed.get("user")
        if not user_str:
            logger.warning("verify_telegram_init_data: 'user' field not found in parsed data.")
            return None
            
        user_data = json.loads(user_str)
        logger.info(f"verify_telegram_init_data: Successful verification for user: {user_data.get('id')}")
        return user_data
    except Exception as e:
        logger.error(f"verify_telegram_init_data: Exception during verification: {e}", exc_info=True)
        return None

def is_admin_user(telegram_id: int) -> bool:
    """Foydalanuvchi bot adminlaridan biri ekanligini tekshiradi"""
    # ADMIN_IDS_RAW ni vergul bo'yicha ajratamiz
    admin_ids = [int(x.strip()) for x in settings.ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]
    is_admin = telegram_id in admin_ids
    logger.info(f"is_admin_user: checking {telegram_id} against {admin_ids} -> Result: {is_admin}")
    return is_admin

async def get_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"), 
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """
    Dasturchilar API so'rovlarida X-API-Key headerini tekshiruvchi FastAPI dependencysi.
    Kalit mavjudligi, faolligi va balansda kamida 1500 so'm borligini tekshiradi.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key sarlavhasi (header) yuborilmadi."
        )
        
    # SHA256 xeshini hisoblaymiz
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    
    # Bazadan qidiramiz
    api_key = await crud.get_api_key_by_hash(db, key_hash)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yuborilgan API kalit yaroqsiz."
        )
        
    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ushbu API kalit faolsizlantirilgan (bloklangan)."
        )
        
    # Balansni tekshiramiz (kamida bitta ovoz berish uchun yetarli pul bo'lishi shart)
    if api_key.balance_uzs < 1500:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="API kalit balansi yetarli emas. Kamida 1 500 so'm bo'lishi shart."
        )
        
    return api_key
