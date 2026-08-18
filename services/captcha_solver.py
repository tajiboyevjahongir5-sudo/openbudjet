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
_keys: list[str] = []
_key_index: int = 0

def _get_next_key() -> Optional[str]:
    """Round-robin usulida keyingi API key'ni qaytaradi"""
    global _keys, _key_index
    if not _keys:
        _keys = _load_keys()
    if not _keys:
        return None
    key = _keys[_key_index % len(_keys)]
    _key_index = (_key_index + 1) % len(_keys)
    return key


# ── Asosiy yechish funksiyasi ─────────────────────────────────────────────────

async def solve_captcha_with_gemini(image_base64: str) -> Optional[int]:
    """
    Captcha rasmini Gemini Vision API orqali yechadi.
    
    :param image_base64: Base64 kodlangan captcha rasmi (data:image/... prefiksi bo'lishi yoki bo'lmasligi mumkin)
    :return: Yechilgan son yoki None (muvaffaqiyatsiz bo'lsa)
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

    # Har bir key bilan urinib ko'ramiz
    for attempt, key in enumerate(_keys):
        try:
            result = await _try_solve_with_key(key, image_bytes)
            if result is not None:
                logger.info(f"Captcha muvaffaqiyatli yechildi: {result} (key #{attempt + 1})")
                return result
        except Exception as e:
            logger.warning(f"Gemini key #{attempt + 1} xatosi: {e}")
            continue

    logger.warning("Barcha Gemini keylari bilan captcha yechib bo'lmadi")
    return None


async def _try_solve_with_key(api_key: str, image_bytes: bytes) -> Optional[int]:
    """Bitta Gemini API key bilan captcha yechishga urinadi"""
    import aiohttp

    # Gemini 1.5 Flash API endpoint
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
            "maxOutputTokens": 10,
        }
    }

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 429:
                logger.warning(f"Gemini rate limit (429) — keyingi key'ga o'tiladi")
                return None
            if resp.status != 200:
                logger.warning(f"Gemini API status: {resp.status}")
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
