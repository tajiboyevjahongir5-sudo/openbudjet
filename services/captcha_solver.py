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

async def solve_with_capsolver(image_base64: str) -> Optional[int]:
    """
    CapSolver.com API (AI-based, extremely fast) orqali matematik captchani yechadi.
    """
    from config import settings
    api_key = settings.CAPSOLVER_API_KEY.strip()
    if not api_key:
        return None

    if not image_base64:
        return None

    if "," in image_base64:
        image_base64 = image_base64.split(",")[-1]

    create_url = "https://api.capsolver.com/createTask"
    
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "ImageToTextTask",
            "body": image_base64
        }
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(create_url, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"CapSolver math createTask status code: {resp.status}")
                    return None
                data = await resp.json()
                if data.get("errorId", 0) != 0:
                    logger.warning(f"CapSolver math createTask error: {data.get('errorDescription')}")
                    return None
                
                solution = data.get("solution", {})
                text = solution.get("text", "").strip()
                if not text:
                    return None
                    
                logger.info(f"CapSolver math raw text: {text}")
                text_clean = "".join(c for c in text if c in "0123456789+-*/")
                if not text_clean:
                    return None
                try:
                    result = eval(text_clean)
                    return int(result)
                except Exception as eval_err:
                    logger.warning(f"CapSolver math eval error for '{text_clean}': {eval_err}")
                    return None
    except Exception as e:
        logger.error(f"CapSolver math captcha exception: {e}")
    return None


async def solve_captcha_with_gemini(image_base64: str) -> Optional[int]:
    """
    Gemini o'rniga CapSolver/2Captcha ishlatadi (backward compatibility).
    """
    return await solve_captcha(image_base64)


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
    1. CapSolver (AI - ~0.2s)
    2. 2Captcha (Zaxira - ~5s)
    """
    if not image_base64:
        return None

    # 1. CapSolver
    res = await solve_with_capsolver(image_base64)
    if res is not None:
        logger.info(f"CapSolver math captcha yechdi: {res}")
        return res

    # 2. 2Captcha
    res = await solve_with_2captcha(image_base64)
    if res is not None:
        logger.info(f"2Captcha math captcha yechdi: {res}")
        return res

    return None


async def solve_mvc_visual_captcha(imgA_b64: str, imgB_b64: str) -> list[dict]:
    """
    Open Budget MVC Initiative rasmiy captchasini yechadi (2 ta harf koordinatasi) — direct 2Captcha coordinates.
    """
    from config import settings
    
    # 2Captcha coordinates solver
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
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post("https://2captcha.com/in.php", data=payload) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("status") == 1:
                        task_id = data.get("request")
                        await asyncio.sleep(5)
                        for _ in range(12):
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


async def solve_recaptcha_v3_capsolver(client_key: str, sitekey: str, pageurl: str, action: str = "submit") -> Optional[str]:
    """
    Solves Google reCAPTCHA v3 using CapSolver API (AI-based, extremely fast).
    """
    create_url = "https://api.capsolver.com/createTask"
    result_url = "https://api.capsolver.com/getTaskResult"
    
    payload = {
        "clientKey": client_key,
        "task": {
            "type": "ReCaptchaV3TaskProxyLess",
            "websiteURL": pageurl,
            "websiteKey": sitekey,
            "pageAction": action,
            "minScore": 0.3
        }
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(create_url, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"CapSolver createTask status code: {resp.status}")
                    return None
                data = await resp.json()
                if data.get("errorId", 0) != 0:
                    logger.warning(f"CapSolver createTask error: {data.get('errorDescription')}")
                    return None
                
                task_id = data.get("taskId")
                if data.get("status") == "ready":
                    token = data.get("solution", {}).get("gRecaptchaResponse")
                    logger.info("CapSolver reCAPTCHA v3 solved instantly on task creation!")
                    return token
                    
            logger.info(f"CapSolver reCAPTCHA v3 task created: {task_id}, polling...")
            for attempt in range(15):
                await asyncio.sleep(1.0)
                res_payload = {
                    "clientKey": client_key,
                    "taskId": task_id
                }
                async with session.post(result_url, json=res_payload) as resp:
                    if resp.status != 200:
                        continue
                    res_data = await resp.json()
                    if res_data.get("errorId", 0) != 0:
                        logger.warning(f"CapSolver getTaskResult error: {res_data.get('errorDescription')}")
                        return None
                    if res_data.get("status") == "ready":
                        token = res_data.get("solution", {}).get("gRecaptchaResponse")
                        logger.info("CapSolver reCAPTCHA v3 solved successfully!")
                        return token
                        
            logger.warning("CapSolver reCAPTCHA v3 polling timeout")
    except Exception as e:
        logger.error(f"CapSolver reCAPTCHA v3 exception: {e}")
    return None


async def solve_recaptcha_v3(sitekey: str, pageurl: str, action: str = "submit") -> Optional[str]:
    """
    Solves Google reCAPTCHA v3 using CapSolver as primary, falling back to 2Captcha on failure.
    """
    try:
        from config import settings
        
        # 1. Try CapSolver first if configured
        capsolver_key = getattr(settings, "CAPSOLVER_API_KEY", "").strip()
        if capsolver_key:
            logger.info("Using CapSolver for reCAPTCHA v3...")
            token = await solve_recaptcha_v3_capsolver(capsolver_key, sitekey, pageurl, action)
            if token:
                return token
            logger.warning("CapSolver failed to solve reCAPTCHA v3, falling back to 2Captcha...")

        # 2. Fallback to 2Captcha
        api_key = getattr(settings, "TWOCAPTCHA_API_KEY", "") or "9b2aa62e71a8d0056aa94e4d6e301f9d"
        if not api_key:
            logger.warning("TWOCAPTCHA_API_KEY is empty, cannot solve reCAPTCHA v3")
            return None
            
        in_url = "https://2captcha.com/in.php"
        res_url = "https://2captcha.com/res.php"
        
        for try_count in range(1, 3):
            try:
                payload = {
                    "key": api_key,
                    "method": "userrecaptcha",
                    "version": "v3",
                    "action": action,
                    "min_score": "0.3",
                    "googlekey": sitekey,
                    "pageurl": pageurl,
                    "json": 1
                }
                
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(in_url, data=payload) as resp:
                        if resp.status != 200:
                            logger.warning(f"2Captcha in.php status code {resp.status} (attempt {try_count})")
                            continue
                        data = await resp.json(content_type=None)
                        if data.get("status") != 1:
                            logger.warning(f"2Captcha in.php error: {data.get('request')} (attempt {try_count})")
                            continue
                        task_id = data.get("request")
                        
                    logger.info(f"reCAPTCHA v3 task created: {task_id}, polling for solution (attempt {try_count})...")
                    
                    for attempt in range(15):
                        await asyncio.sleep(2)
                        params = {
                            "key": api_key,
                            "action": "get",
                            "id": task_id,
                            "json": 1
                        }
                        async with session.get(res_url, params=params) as resp:
                            if resp.status != 200:
                                continue
                            res_data = await resp.json(content_type=None)
                            if res_data.get("status") == 1:
                                token = res_data.get("request")
                                logger.info("reCAPTCHA v3 solved successfully via 2Captcha!")
                                return token
                            elif res_data.get("request") != "CAPCHA_NOT_READY":
                                logger.warning(f"2Captcha res.php error: {res_data.get('request')} (attempt {try_count})")
                                break
                                
                    logger.warning(f"2Captcha reCAPTCHA v3 polling timeout/unsolvable on attempt {try_count}")
            except Exception as e:
                logger.error(f"Error solving reCAPTCHA v3 via 2Captcha on attempt {try_count}: {e}")
    except Exception as ge:
        logger.error(f"Global error solving reCAPTCHA v3: {ge}")
    return None


