"""
Captcha avtomatik yechish servisi — Gemini Vision API yordamida.

Ishlash tartibi:
1. Captcha rasmi (base64) Gemini Vision'ga yuboriladi
2. Gemini math ifodani o'qib, javob raqamini qaytaradi
3. Agar Gemini xato qilsa — keyingi API key bilan qayta urinadi
4. Barcha keylar tugasa — None qaytaradi (foydalanuvchiga ko'rsatiladi)

API key limiti: har bir key uchun 1500 so'rov/kun
8 key = 12,000 so'rov/kun = ~6,000 ta avtomatik ovoz/kun
"""

import logging
import base64
import asyncio
import re
import aiohttp
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)

# ── Gemini keylarni config dan olish ──────────────────────────────────────────

def _load_keys() -> list[str]:
    """GEMINI_API_KEYS env o'zgaruvchisidan keylarni yuklaydi"""
    try:
        from config import settings
        raw = settings.GEMINI_API_KEYS.strip()
        if not raw:
            return []
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        logger.info(f"Gemini captcha solver: {len(keys)} ta API key yuklandi")
        return keys
    except Exception as e:
        logger.warning(f"Gemini API keys yuklanmadi: {e}")
        return []

# Keylarni global holatda saqlaymiz va round-robin qilamiz
import time

_keys: list[str] = []
_keys_verified: bool = False
_key_index: int = 0
_blocked_keys: dict[str, float] = {}

def block_key(api_key: str, duration: float = 120.0):
    """Kalitni ma'lum muddatga bloklaydi (429 xato bo'lsa)"""
    _blocked_keys[api_key] = time.time() + duration
    logger.warning(f"Gemini API key blocked for {duration} seconds: {api_key[:10]}...")

def get_available_keys() -> list[str]:
    """Bloklanmagan faol kalitlar ro'yxatini qaytaradi"""
    global _keys
    now = time.time()
    # Bloklash muddati tugaganlarni tozalaymiz
    active_blocked = {k: ts for k, ts in _blocked_keys.items() if ts > now}
    _blocked_keys.clear()
    _blocked_keys.update(active_blocked)
    
    available = [k for k in _keys if k not in _blocked_keys]
    # Agar hamma kalitlar bloklangan bo'lsa, hammasini qaytaramiz (zaxira sifatida)
    if not available:
        return _keys
    return available

async def verify_all_api_keys():
    """
    Tizimdagi barcha API kalitlarni bir marta tezkor tekshiradi
    va faqat ishlaydigan hamda tezkor javob beradigan kalitlarni ro'yxatda qoldiradi.
    """
    global _keys, _keys_verified
    _keys = _load_keys()
    if not _keys:
        _keys_verified = True
        return

    logger.info("Gemini API kalitlarini liveness check tekshiruvi boshlandi...")
    
    async def check_key(key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={key}"
        payload = {
            "contents": [{"parts": [{"text": "Hello, respond with OK"}]}]
        }
        # 6 soniya kutish limiti (agar sekin yoki yaroqsiz bo'lsa tashlab yuboramiz)
        timeout = aiohttp.ClientTimeout(total=6)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return key
                    elif resp.status == 429:
                        block_key(key, 120.0)
                        return key
                    else:
                        logger.warning(f"Gemini API key check failed ({resp.status}) for key: {key[:10]}...")
        except Exception as e:
            logger.warning(f"Gemini API key check error ({e.__class__.__name__}) for key: {key[:10]}...")
        return None

    tasks = [check_key(key) for key in _keys]
    results = await asyncio.gather(*tasks)
    
    working_keys = [k for k in results if k is not None]
    
    _keys = working_keys
    _keys_verified = True
    logger.info(f"Gemini Liveness Check tugadi: {len(working_keys)} ta faol/ishlaydigan kalitlar qoldi.")

def _get_next_key() -> Optional[str]:
    """Round-robin usulida keyingi API key'ni qaytaradi"""
    global _keys, _key_index, _keys_verified
    if not _keys and not _keys_verified:
        _keys = _load_keys()
    if not _keys:
        return None
    key = _keys[_key_index % len(_keys)]
    _key_index = (_key_index + 1) % len(_keys)
    return key


# ── Asosiy yechish funksiyasi ─────────────────────────────────────────────────

async def solve_captcha_with_gemini(image_base64: str) -> Optional[int]:
    """
    Captcha rasmini ketma-ket (sequential) ravishda API keylar orqali yechadi.
    Bu orqali keylar limitini (429 xatolarini) tejab qolamiz.
    """
    global _keys
    if not _keys:
        _keys = _load_keys()
    if not _keys:
        logger.warning("Gemini API keylari yo'q — captcha qo'lda yechiladi")
        return None

    # Base64 prefiksni tozalaymiz
    if "," in image_base64:
        image_base64 = image_base64.split(",")[-1]

    # Rasm baytlarini olamiz
    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        logger.error(f"Captcha rasmi base64 decode xatosi: {e}")
        return None

    # Navbatdagi 3 ta kalitni ketma-ket urinib ko'rish uchun tanlaymiz
    global _key_index
    available_keys = get_available_keys()
    n_keys = len(available_keys)
    
    # Maksimal 3 ta turli kalit bilan ketma-ket urinib ko'ramiz
    for _ in range(min(3, n_keys)):
        key = available_keys[_key_index % n_keys]
        _key_index = (_key_index + 1) % n_keys
        
        try:
            res = await _try_solve_with_key(key, image_bytes)
            if res is not None:
                return res
        except Exception as e:
            logger.warning(f"Key {_key_index} orqali captcha yechishda xatolik yuz berdi: {e}")

    logger.warning("Barcha tanlangan Gemini kalitlari muvaffaqiyatsiz yakunlandi (yoki limitga uchradi)")
    return None


async def _try_solve_with_key(api_key: str, image_bytes: bytes) -> Optional[int]:
    """Bitta Gemini API key bilan captcha yechishga urinadi"""
    import aiohttp

    # Gemini 3.6 Flash API endpoint (newer models support AQ and AIza keys on all regions)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"

    # Rasmni base64 ga o'tkazamiz
    image_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": image_b64
                    }
                },
                {
                    "text": (
                        "Bu rasmdagi matematik ifodani yech va faqat son javobini yoz. "
                        "Masalan: '12 + 5' bo'lsa '17' deb yoz. "
                        "Hech qanday qo'shimcha matn yozma, faqat son."
                    )
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 150,
        }
    }

    # Tezkor ishlash uchun timeoutni 8 soniya qilamiz
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 429:
                block_key(api_key, 120.0)
                return None
            if resp.status != 200:
                return None

            data = await resp.json()

    # Javobni parse qilamiz
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Faqat raqamlarni ajratamiz
        numbers = re.findall(r'\d+', text)
        if numbers:
            return int(numbers[0])
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"Gemini javobini parse qilib bo'lmadi: {e}, javob: {data}")

    return None


async def solve_with_capmonster(image_base64: str) -> Optional[int]:
    """
    CapMonster.cloud API orqali captchani yechadi.
    100% ishonchli va barqaror pullik yechim (1000 ta captcha = $0.30 - $0.60).
    """
    from config import settings
    api_key = settings.CAPMONSTER_API_KEY.strip()
    if not api_key:
        return None

    # Base64 prefiksni tozalaymiz
    if "," in image_base64:
        image_base64 = image_base64.split(",")[-1]

    create_url = "https://api.capmonster.cloud/createTask"
    result_url = "https://api.capmonster.cloud/getTaskResult"

    payload = {
        "clientKey": api_key,
        "task": {
            "type": "ImageToTextTask",
            "body": image_base64
        }
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Task yaratamiz
            async with session.post(create_url, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"CapMonster createTask failed status: {resp.status}")
                    return None
                data = await resp.json()
                if data.get("errorId", 0) != 0:
                    logger.warning(f"CapMonster createTask error: {data.get('errorCode')}")
                    return None
                task_id = data.get("taskId")

            if not task_id:
                return None

            # 2. Polling (natijani kutamiz)
            result_payload = {
                "clientKey": api_key,
                "taskId": task_id
            }

            for _ in range(5):  # maks 5 marta tekshiramiz (jami ~6 soniya)
                await asyncio.sleep(1.2)
                async with session.post(result_url, json=result_payload) as resp:
                    if resp.status != 200:
                        continue
                    res_data = await resp.json()
                    if res_data.get("errorId", 0) != 0:
                        logger.warning(f"CapMonster getTaskResult error: {res_data.get('errorCode')}")
                        return None
                    
                    status = res_data.get("status")
                    if status == "ready":
                        solution = res_data.get("solution", {}).get("text", "")
                        # Faqat raqamlarni ajratamiz (chunki matematika javobi doim raqam)
                        numbers = re.findall(r'\d+', solution)
                        if numbers:
                            logger.info(f"CapMonster captcha yechdi: {numbers[0]}")
                            return int(numbers[0])
                        return None
    except Exception as e:
        logger.error(f"CapMonster captcha solving error: {e}")
    return None


async def solve_with_2captcha(image_base64: str) -> Optional[int]:
    """
    2Captcha.com (RuCaptcha) API orqali captchani yechadi.
    100% ishonchli pullik muqobil (1000 ta captcha = $1.00).
    To'lov tizimlari juda ko'p (PerfectMoney, Payeer, AdvCash, xalqaro kartalar va mahalliy dilerlar).
    """
    from config import settings
    api_key = settings.TWOCAPTCHA_API_KEY.strip()
    if not api_key:
        logger.warning("TWOCAPTCHA_API_KEY sozlanmagan — 2Captcha o'tkazib yuborildi")
        return None

    # Base64 prefiksni tozalaymiz
    if "," in image_base64:
        image_base64 = image_base64.split(",")[-1]

    create_url = "https://2captcha.com/in.php"
    result_url = "https://2captcha.com/res.php"

    payload = {
        "key": api_key,
        "method": "base64",
        "body": image_base64,
        "json": 1
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Task yuboramiz
            async with session.post(create_url, data=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"2Captcha in.php failed status: {resp.status}")
                    return None
                data = await resp.json()
                if data.get("status") != 1:
                    logger.warning(f"2Captcha in.php error: {data.get('request')}")
                    return None
                task_id = data.get("request")

            if not task_id:
                return None

            # 2. Polling (natijani kutamiz)
            params = {
                "key": api_key,
                "action": "get",
                "id": task_id,
                "json": 1
            }

            for _ in range(6):  # maks 6 marta tekshiramiz (jami ~8 soniya)
                await asyncio.sleep(1.3)
                async with session.get(result_url, params=params) as resp:
                    if resp.status != 200:
                        continue
                    res_data = await resp.json()
                    if res_data.get("status") == 1:
                        solution = res_data.get("request", "")
                        # Faqat raqamlarni ajratamiz (matematik javob)
                        numbers = re.findall(r'\d+', solution)
                        if numbers:
                            logger.info(f"2Captcha captcha yechdi: {numbers[0]}")
                            return int(numbers[0])
                        return None
                    elif res_data.get("request") != "CAPCHA_NOT_READY":
                        logger.warning(f"2Captcha res.php error: {res_data.get('request')}")
                        return None
    except Exception as e:
        logger.error(f"2Captcha captcha solving error: {e}")
    return None


async def solve_captcha(image_base64: str) -> Optional[int]:
    """
    Asosiy captcha yechish funksiyasi.
    Avval 2Captcha bilan urinadi (sozlangan bo'lsa), keyin Gemini'ga o'tadi (fallback).
    """
    # 1. 2Captcha ni tekshiramiz
    res = await solve_with_2captcha(image_base64)
    if res is not None:
        return res
        
    # 2. Gemini ni tekshiramiz (fallback)
    return await solve_captcha_with_gemini(image_base64)
