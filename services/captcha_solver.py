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
_keys: list[str] = []
_keys_verified: bool = False
_key_index: int = 0

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
    Captcha rasmini parallel ravishda bir nechta Gemini API keylar orqali yechadi (Tezkorlik uchun).
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

    # Navbatdagi 3 ta kalitni parallel ishlash uchun tanlaymiz (Round-robin)
    global _key_index
    n_keys = len(_keys)
    selected_keys = []
    for _ in range(min(3, n_keys)):
        selected_keys.append(_keys[_key_index % n_keys])
        _key_index = (_key_index + 1) % n_keys

    # Parallel so'rovlarni yaratamiz
    tasks = [
        asyncio.create_task(_try_solve_with_key(key, image_bytes))
        for key in selected_keys
    ]

    # Birinchi bo'lib muvaffaqiyatli kelgan javobni qabul qilamiz
    result = None
    for finished_task in asyncio.as_completed(tasks):
        try:
            res = await finished_task
            if res is not None:
                result = res
                # Muvaffaqiyatli natija olgach, qolgan parallel vazifalarni bekor qilamiz
                for t in tasks:
                    if not t.done():
                        t.cancel()
                break
        except Exception as e:
            logger.warning(f"Parallel Gemini so'rovida xatolik: {e}")

    if result is not None:
        return result

    logger.warning("Barcha parallel Gemini so'rovlari muvaffaqiyatsiz yakunlandi")
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
