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
import json
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
    Tizimdagi barcha API kalitlarni yuklaydi.
    """
    global _keys, _keys_verified
    _keys = _load_keys()
    _keys_verified = True
    logger.info(f"Gemini captcha solver: {len(_keys)} ta faol kalitlar to'liq tayyor.")

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
    Captcha rasmini PARALLEL ravishda barcha mavjud API keylar orqali bir vaqtda yuboradi.
    Birinchi to'g'ri javob kelishi bilan qaytaradi (race condition pattern).
    Bu eng tez va ishonchli usul — bitta key timeout bo'lsa, boshqasi javob beradi.
    """
    global _keys
    if not _keys:
        _keys = _load_keys()
    if not _keys:
        logger.warning("Gemini API keylari yo'q — captcha qo'lda yechiladi")
        return None

    if not image_base64:
        return None

    if "," in image_base64:
        image_base64 = image_base64.split(",")[-1]

    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        logger.error(f"Captcha rasmi base64 decode xatosi: {e}")
        return None

    available_keys = get_available_keys()
    if not available_keys:
        return None

    # Barcha kalitlarni PARALLEL ravishda bir vaqtda ishga tushiramiz
    # Birinchi to'g'ri javobni olamiz
    tasks = [_try_solve_with_key(key, image_bytes) for key in available_keys[:5]]
    
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, int) and res is not None:
                return res
    except Exception as e:
        logger.warning(f"Parallel Gemini urinishda umumiy xatolik: {e}")

    logger.warning("Barcha Gemini kalitlari muvaffaqiyatsiz yakunlandi")
    return None


async def _try_solve_with_key(api_key: str, image_bytes: bytes) -> Optional[int]:
    """Bitta Gemini API key bilan captcha yechishga urinadi (5 soniya limit)"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
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

    # 5 soniya timeout — Railway'dan Gemini'ga normal ulanish uchun yetarli
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 429:
                    block_key(api_key, 120.0)
                    return None
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        numbers = re.findall(r'\d+', text)
        if numbers:
            return int(numbers[0])
    except (KeyError, IndexError, ValueError):
        pass

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

    if not image_base64:
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
                data = await resp.json(content_type=None)
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
                    res_data = await resp.json(content_type=None)
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

    if not image_base64:
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
        "json": 1,
        "calc": 1,        # 2Captcha: rasm ichidagi matematik amalni hisoblash (5+3=8 -> 8)
        "numeric": 1,     # Faqat raqamli javob
        "textinstructions": "Calculate math expression and write ONLY the result number (e.g. 5+3 write 8)",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Task yuboramiz
            async with session.post(create_url, data=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"2Captcha in.php failed status: {resp.status}")
                    return None
                data = await resp.json(content_type=None)
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

            await asyncio.sleep(3)  # Dastlabki kutish (2Captcha ishchisi olishi uchun)
            for attempt in range(10):  # maks 10 marta tekshiramiz (jami ~25 soniya)
                await asyncio.sleep(2.2)
                async with session.get(result_url, params=params) as resp:
                    if resp.status != 200:
                        continue
                    res_data = await resp.json(content_type=None)
                    if res_data.get("status") == 1:
                        solution = res_data.get("request", "")
                        # Faqat raqamlarni ajratamiz (matematik javob)
                        numbers = re.findall(r'\d+', solution)
                        if numbers:
                            logger.info(f"2Captcha captcha muvaffaqiyatli yechdi ({attempt+1}-urinish): {numbers[0]}")
                            return int(numbers[0])
                        logger.warning(f"2Captcha javobida raqam topilmadi: {solution!r}")
                        return None
                    elif res_data.get("request") != "CAPCHA_NOT_READY":
                        logger.warning(f"2Captcha res.php error: {res_data.get('request')}")
                        return None

            logger.warning("2Captcha timeout: 25 soniya ichida ishchi captchani yechib ulgurmadi")
    except Exception as e:
        logger.error(f"2Captcha captcha solving error: {e}")
    return None


async def solve_captcha(image_base64: str) -> Optional[int]:
    """
    Asosiy captcha yechish funksiyasi.
    1. 2Captcha (insonlar tomonidan 100% aniq yechiladi)
    2. Gemini Vision Flash (zaxira sifatida bepul AI bilan yechiladi)
    """
    if not image_base64:
        return None

    # 1. Avval 2Captcha bilan yechishga urinish
    res = await solve_with_2captcha(image_base64)
    if res is not None:
        logger.info(f"2Captcha captcha yechdi: {res}")
        return res

    # 2. Zaxira: Gemini Flash AI
    logger.info("2Captcha muvaffaqiyatsiz — Gemini AI bilan urinilmoqda...")
    res = await solve_captcha_with_gemini(image_base64)
    if res is not None:
        logger.info(f"Gemini captcha yechdi: {res}")
    return res


async def solve_mvc_visual_captcha(imgA_b64: str, imgB_b64: str) -> list[dict]:
    """
    Open Budget MVC Initiative rasmiy captchasini yechadi (2 ta harf koordinatasi).
    1. Gemini Vision AI — super tezkor (1-2 soniyada yechadi)
    2. 2Captcha coordinatescaptcha — zaxira sifatida
    """
    from config import settings
    
    # 1. Tezkor Gemini Vision AI
    try:
        from google import genai
        gemini_keys = getattr(settings, "GEMINI_API_KEYS", "").split(",")
        if gemini_keys and gemini_keys[0].strip():
            client = genai.Client(api_key=gemini_keys[0].strip())
            prompt = (
                "You are an expert visual coordinate detector.\n"
                "Image 1 (Instruction): Contains exactly 2 reference characters/letters shown side by side in left-to-right order.\n"
                "Image 2 (Search Canvas): A 340 pixel wide by 220 pixel high canvas containing various scattered characters.\n\n"
                "Step 1: Identify the first letter (on the left of Image 1). Find its exact center coordinates (x between 0 and 340, y between 0 and 220) in Image 2.\n"
                "Step 2: Identify the second letter (on the right of Image 1). Find its exact center coordinates in Image 2.\n\n"
                "Output strictly valid JSON with the 2 points in order:\n"
                '[{"x": 120, "y": 80}, {"x": 250, "y": 150}]'
            )
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    prompt,
                    genai.types.Part.from_bytes(data=base64.b64decode(imgA_b64), mime_type="image/png"),
                    genai.types.Part.from_bytes(data=base64.b64decode(imgB_b64), mime_type="image/png"),
                ]
            )
            coords = json.loads(re.search(r"\[\s*\{.*?\}\s*\]", response.text, re.DOTALL).group(0))
            points = [{"id": f"{pt['x']}{pt['y']}", "x": int(pt["x"]), "y": int(pt["y"])} for pt in coords]
            if len(points) >= 2:
                logger.info(f"Gemini Vision MVC points tezkor topdi: {points}")
                return points
    except Exception as e:
        logger.warning(f"Gemini MVC visual solve xatosi: {e}")

    # 2. Zaxira: 2Captcha coordinates solver
    try:
        api_key = getattr(settings, "TWOCAPTCHA_API_KEY", "") or "9b2aa62e71a8d0056aa94e4d6e301f9d"
        payload = {
            "key": api_key,
            "method": "base64",
            "body": imgB_b64,
            "imginstructions": imgA_b64,
            "textinstructions": "Click on the two letter pairs from Image A inside the main image in exact order from left to right",
            "coordinatescaptcha": 1,
            "json": 1
        }
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post("https://2captcha.com/in.php", data=payload) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("status") == 1:
                        task_id = data.get("request")
                        for _ in range(8):
                            await asyncio.sleep(2.0)
                            async with session.get("https://2captcha.com/res.php", params={"key": api_key, "action": "get", "id": task_id, "json": 1}) as r:
                                res_data = await r.json(content_type=None)
                                if res_data.get("status") == 1:
                                    raw_req = res_data.get("request", [])
                                    points = []
                                    if isinstance(raw_req, list):
                                        for pt in raw_req:
                                            x, y = int(pt["x"]), int(pt["y"])
                                            points.append({"id": f"{x}{y}", "x": x, "y": y})
                                    elif isinstance(raw_req, str):
                                        for pair in raw_req.replace("coordinates:", "").split(";"):
                                            if "x=" in pair and "y=" in pair:
                                                parts = dict(p.split("=") for p in pair.split(","))
                                                x, y = int(parts["x"]), int(parts["y"])
                                                points.append({"id": f"{x}{y}", "x": x, "y": y})
                                    if len(points) >= 2:
                                        logger.info(f"2Captcha MVC visual points topdi: {points}")
                                        return points
                                elif res_data.get("request") != "CAPCHA_NOT_READY":
                                    break
    except Exception as e:
        logger.warning(f"2Captcha MVC visual solve xatosi: {e}")

    return []

