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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
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

    # Gemini 1.5 Flash API endpoint (newer models support AQ and AIza keys on all regions)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

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


async def solve_captcha(image_base64: str) -> Optional[int]:
    """
    Asosiy captcha yechish funksiyasi.
    Avval Gemini bilan urinadi, muvaffaqiyatsiz bo'lsa None qaytaradi.
    """
    return await solve_captcha_with_gemini(image_base64)
