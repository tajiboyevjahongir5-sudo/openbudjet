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
    user_data, _ = verify_telegram_init_data_detailed(init_data)
    return user_data

def verify_telegram_init_data_detailed(init_data: str) -> tuple[dict | None, str | None]:
    """
    initData imzosini tekshiradi va batafsil xatolik xabarini qaytaradi.
    """
    if not init_data:
        return None, "initData is empty"
        
    try:
        # Mini App raw data strings might be URL-encoded, let's decode once if it is double encoded
        if init_data.startswith("query_id%3D") or "user%3D" in init_data or "hash%3D" in init_data:
            init_data = urllib.parse.unquote(init_data)
            
        # Parse the raw pairs to find the hash
        pairs = init_data.split("&")
        parsed_dict = {}
        tg_hash = None
        other_pairs = []
        
        for pair in pairs:
            if not pair or "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if k == "hash":
                tg_hash = v
            else:
                other_pairs.append((k, v))
                parsed_dict[k] = urllib.parse.unquote(v)
                
        if not tg_hash:
            return None, "hash field not found"
            
        # Sort keys alphabetically and reconstruct data_check_string using the original raw values
        other_pairs.sort(key=lambda x: x[0])
        data_check_string = "\n".join(f"{k}={v}" for k, v in other_pairs)
        
        # Secret key yaratamiz (HMAC-SHA256 "WebappData" bilan)
        secret_key = hmac.new(b"WebappData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        
        # InitData imzosini hisoblaymiz
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != tg_hash:
            token_masked = f"{settings.BOT_TOKEN[:6]}...{settings.BOT_TOKEN[-6:]}" if settings.BOT_TOKEN else "None"
            logger.warning(f"verify_telegram_init_data: Signature mismatch! Calculated: {calculated_hash}, Received: {tg_hash}")
            return None, f"Signature mismatch (Token: {token_masked})"
            
        # User ma'lumotlarini JSON qilib o'qiymiz
        user_str = parsed_dict.get("user")
        if not user_str:
            return None, "user field not found"
            
        user_data = json.loads(user_str)
        return user_data, None
    except Exception as e:
        logger.error(f"verify_telegram_init_data: Exception: {e}", exc_info=True)
        return None, f"Exception: {str(e)}"


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


import time

def generate_admin_token(telegram_id: int) -> str:
    """Admin uchun 24 soat davomida amal qiladigan vaqtinchalik xavfsiz token yaratadi"""
    timestamp = int(time.time() // 86400) # Kunlik o'zgaruvchi
    message = f"{telegram_id}:{timestamp}"
    signature = hmac.new(settings.BOT_TOKEN.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{telegram_id}:{timestamp}:{signature}"

def verify_admin_token(token: str) -> int | None:
    """Vaqtinchalik admin tokenini tekshiradi va to'g'ri bo'lsa telegram_id ni qaytaradi"""
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        telegram_id = int(parts[0])
        token_timestamp = int(parts[1])
        signature = parts[2]
        
        current_timestamp = int(time.time() // 86400)
        # 1 kunlik farq bilan (bugungi yoki kechagi token bo'lsa) qabul qiladi
        if abs(current_timestamp - token_timestamp) > 1:
            return None
            
        message = f"{telegram_id}:{token_timestamp}"
        expected_signature = hmac.new(settings.BOT_TOKEN.encode(), message.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_signature, signature):
            return telegram_id
    except Exception:
        pass
    return None

