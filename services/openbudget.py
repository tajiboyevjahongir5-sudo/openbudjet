import logging
import asyncio
import aiohttp
import base64
import time
import re
import json
import urllib.parse
from config import settings

logger = logging.getLogger(__name__)

def mask_sensitive_data(data: dict) -> dict:
    """Loglarga chiqib ketadigan maxfiy ma'lumotlarni maskalaydi"""
    if not isinstance(data, dict):
        return data
    masked = data.copy()
    for key in ['access_token', 'refreshToken', 'refresh_token', 'token', 'otpKey', 'otp_key']:
        if key in masked:
            val = str(masked[key])
            masked[key] = f"{val[:10]}...[MASKED]" if len(val) > 15 else "[MASKED]"
    for key in ['phone_number', 'phone']:
        if key in masked:
            val = str(masked[key])
            masked[key] = f"{val[:5]}***{val[-4:]}" if len(val) >= 9 else "[MASKED]"
    for key in ['otp_code', 'code']:
        if key in masked:
            masked[key] = "[MASKED]"
    return masked

class OpenBudgetService:
    """
    openbudget.uz saytining rasmiy JS kodlaridan aniqlangan real ovoz berish API xizmati.
    """
    _session: aiohttp.ClientSession | None = None

    @classmethod
    async def _get_session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=30)
            cls._session = aiohttp.ClientSession(connector=connector)
        return cls._session

    @classmethod
    async def close_session(cls):
        if cls._session and not cls._session.closed:
            await cls._session.close()
    @classmethod
    def _get_url(cls, path: str) -> str:
        """
        So'rov URL ni aniqlaydi:
        - PROXY_URL sozlangan → openbudget.uz to'g'ridan (proksi aiohttp param sifatida)
        - CLOUDFLARE_PROXY_URL sozlangan → Cloudflare Worker orqali
        - Hech biri yo'q → openbudget.uz to'g'ridan
        """
        if settings.PROXY_URL:
            base = "https://openbudget.uz/api"
        elif settings.CLOUDFLARE_PROXY_URL:
            base = settings.CLOUDFLARE_PROXY_URL.rstrip('/')
        else:
            base = "https://openbudget.uz/api"
        return f"{base}{path}"

    @classmethod
    def _get_direct_url(cls, path: str) -> str:
        """To'g'ridan-to'g'ri openbudget.uz URL"""
        return f"https://openbudget.uz/api{path}"

    # 1. Captcha olish manzili (GET)
    @classmethod
    def captcha_url(cls) -> str:
        return cls._get_url("/v2/vote/captcha-2")

    # 2. Login va OTP yuborish (POST)
    @classmethod
    def send_otp_url(cls) -> str:
        return cls._get_url("/v1/login/send-otp")

    # 3. OTP tasdiqlash va token olish (POST)
    @classmethod
    def verify_otp_url(cls) -> str:
        return cls._get_url("/v1/login/verify-otp")

    # 4. Ro'yxatdan o'tish OTP yuborish (POST)
    @classmethod
    def register_send_otp_url(cls) -> str:
        return cls._get_url("/v1/register/send-otp")

    # 5. Ro'yxatdan o'tish OTP tasdiqlash (POST)
    @classmethod
    def register_verify_otp_url(cls) -> str:
        return cls._get_url("/v1/register/verify-otp")

    # 6. Ovozni tasdiqlab yakunlash (POST)
    @classmethod
    def cast_vote_url(cls) -> str:
        return cls._get_url("/v2/info/get-initiative-token")

    @staticmethod
    def _access_captcha_token() -> str:
        """Access-Captcha headerini generatsiya qilish"""
        ts = str(int(time.time() * 1000))
        raw = f"openbudget-captcha-{ts}"
        return base64.b64encode(raw.encode()).decode()

    @classmethod
    async def _execute_request(
        cls,
        method: str,
        url: str,
        headers: dict,
        json_data: dict | None = None,
        timeout_seconds: int = 25
    ) -> tuple[int, dict, str]:
        """
        HTTP so'rovini bajaradi.
        - url: _get_url() dan keladi (PROXY_URL → openbudget.uz, CF_URL → Worker)
        - PROXY_URL sozlangan bo'lsa, aiohttp'ga proxy param sifatida beriladi
        """
        session = await cls._get_session()

        # PROXY_URL ni http:// ga normalizatsiya
        proxy: str | None = None
        if settings.PROXY_URL:
            raw = settings.PROXY_URL.strip()
            proxy = "http://" + raw[len("https://"):] if raw.startswith("https://") else raw

        req_headers = dict(headers)
        req_headers["ngrok-skip-browser-warning"] = "1"

        kw: dict = {"headers": req_headers, "timeout": aiohttp.ClientTimeout(total=timeout_seconds, connect=15)}
        if proxy:
            kw["proxy"] = proxy
        if json_data is not None:
            kw["json"] = json_data

        req = session.get if method.upper() == "GET" else session.post
        try:
            async with req(url, **kw) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                return status, data, ""
        except Exception as e:
            logger.error(f"So'rov xatosi ({url[:60]}): {e.__class__.__name__}: {e}")
            raise

    @classmethod
    async def get_captcha(cls) -> tuple[bool, str, dict | None]:
        """
        GET /v2/vote/captcha-2
        Captcha rasmi va kalitini yuklab oladi (proksi bilan 2 marta urinish).
        """
        if settings.MOCK_OPENBUDGET:
            return True, "ok", {"key": "mock_captcha_key", "image_base64": None, "mock": True}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
            "Access-Captcha": cls._access_captcha_token(),
        }
        
        for attempt in range(2):
            try:
                status, data, text = await cls._execute_request("GET", cls.captcha_url(), headers=headers, timeout_seconds=20)
                if status == 200 and data.get("captchaKey"):
                    return True, "ok", {
                        "key": data.get("captchaKey"),
                        "image_base64": data.get("image"),
                    }
                logger.warning(f"Captcha yuklash urinishi {attempt+1} xatosi: status={status}")
            except Exception as e:
                logger.warning(f"Captcha yuklash urinishi {attempt+1} tarmoq xatosi: {e}")
                if attempt == 0:
                    await asyncio.sleep(1)
        
        return False, "Captcha yuklashda tarmoq xatoligi yuz berdi.", None

    @classmethod
    async def check_and_send_sms(
        cls,
        phone_number: str,
        project_id: str,
        captcha_key: str | None = None,
        captcha_result: int | None = None
    ) -> tuple[bool, str, dict | None]:
        """
        POST /v1/login/send-otp
        Telefonga tasdiqlash SMS kodini yuboradi.
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]

        # Captcha har doim birinchi urinishda talab qilinadi (ham mock, ham real rejimda)
        if captcha_key is None:
            return False, "captcha_required", {"phone": clean_phone, "project_id": project_id}

        # --- MOCK REJIM ---
        if settings.MOCK_OPENBUDGET:
            if clean_phone.endswith("99"):
                return False, "Bu raqam orqali allaqachon ovoz berilgan", {"code": "already_voted"}
            if clean_phone.endswith("00"):
                # Simulation for unregistered user
                return False, "not_registered", {"phone": "998" + clean_phone, "project_id": project_id}
            return True, "SMS kod yuborildi (Simulyatsiya kodi: 1111)", {
                "phone": "998" + clean_phone,
                "project_id": project_id,
                "otp_key": "mock_otp_key",
                "otp_code": "1111"
            }

        # --- REAL REJIM ---
        # 1. Avval rasmiy MVC Loyiha ovoz berish oqimini sinab ko'ramiz
        mvc_ok, mvc_msg, mvc_session = await cls.send_mvc_initiative_sms(clean_phone, project_id)
        if mvc_ok:
            return True, "SMS tasdiqlash kodi yuborildi.", mvc_session

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
        }
        payload = {
            "phone_number": "998" + clean_phone,
            "captcha_key": captcha_key,
            "captcha_result": int(captcha_result) if captcha_result is not None else 0,
        }
        try:
            status, data, text = await cls._execute_request("POST", cls.send_otp_url(), headers=headers, json_data=payload)
            logger.info(f"send-otp javob: {status} — {mask_sensitive_data(data)}")

            if status == 200:
                return True, "SMS tasdiqlash kodi yuborildi.", {
                    "flow": "login",
                    "phone": "998" + clean_phone,
                    "project_id": project_id,
                    "otp_key": data.get("otpKey"),
                }
            elif status == 429:
                retry = data.get("retryAfter", 60)
                return False, f"Juda ko'p urinish. {retry} soniyadan keyin qayta urinib ko'ring.", None
            elif status in (500, 502, 503, 504):
                return False, "server_error", None
            else:
                msg = (data.get("message") or data.get("detail") or f"Status: {status}").strip()
                msg_lower = msg.lower()

                # 1. Foydalanuvchi ro'yxatdan o'tmagan holati (Lotin va Kirill tillarida)
                unregistered_keywords = [
                    "ro'yxatdan o'tmagan", "topilmadi", "not found", "not registered", "mavjud emas", "ro‘yxatdan", "foydalanuvchi",
                    "топилмади", "фойдаланувчи", "рўйхатдан", "маълумотлари топилмади", "топилмаган", "мавжуд эмас", "ҳеч қандай"
                ]
                if any(term in msg_lower for term in unregistered_keywords):
                    return False, "not_registered", {"phone": "998" + clean_phone, "project_id": project_id}

                # 2. Allaqachon ovoz berilgan (boshqa yoki shu raqam orqali)
                already_voted_keywords = [
                    "ovoz bergan", "ovoz berilgan", "already voted", "boshqa raqam",
                    "овоз берган", "овоз берилган", "бошқа рақам"
                ]
                if any(term in msg_lower for term in already_voted_keywords):
                    return False, "already_voted", {"phone": clean_phone, "detail": msg}

                return False, f"Xatolik: {msg}", None
        except Exception as e:
            logger.error(f"Send OTP Error: {e}")
    @classmethod
    async def send_mvc_initiative_sms(
        cls,
        phone_number: str,
        project_id: str
    ) -> tuple[bool, str, dict | None]:
        """
        Open Budget MVC /api/v2/vote/mvc/captcha orqali to'g'ridan-to'g'ri
        rasmiy LOYIHA OVOZ BERISH SMS-ini yuboradi.
        """
        from services.captcha_solver import solve_mvc_visual_captcha
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]
            
        target_uuid = project_id
        if "-" not in str(target_uuid):
            info = await cls.find_initiative(str(project_id))
            if info and info.get("id"):
                target_uuid = info.get("id")
                
        formatted_phone = f"{clean_phone[:2]} {clean_phone[2:5]}-{clean_phone[5:7]}-{clean_phone[7:]}"
        mvc_url = f"https://openbudget.uz/api/v2/vote/mvc/captcha/{target_uuid}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": "https://openbudget.uz",
            "Referer": f"https://openbudget.uz/initiative/{project_id}",
        }
        
        jar = aiohttp.CookieJar(unsafe=True)
        proxy = settings.PROXY_URL or None
        
        for attempt in range(4):
            try:
                jar = aiohttp.CookieJar(unsafe=True)
                timeout = aiohttp.ClientTimeout(total=25)
                async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
                    async with session.get(mvc_url, headers=headers, proxy=proxy) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text()
                        
                    srcs = re.findall(r'<img[^>]+src="(data:image/[^"]+)"', html)
                    if len(srcs) < 2:
                        srcs = re.findall(r"src='(data:image/[^']+)'", html)
                    if len(srcs) < 2:
                        continue
                        
                    img_a_b64 = srcs[0].split(",")[-1]
                    img_b_b64 = srcs[1].split(",")[-1]
                    
                    points = await solve_mvc_visual_captcha(img_a_b64, img_b_b64)
                    if not points or len(points) < 2:
                        continue
                        
                    post_url = "https://openbudget.uz/api/v2/vote/mvc/captcha"
                    post_data = {
                        "phoneNumber": formatted_phone,
                        "points": json.dumps(points, separators=(",", ":")),
                    }
                    post_headers = dict(headers)
                    post_headers["Referer"] = mvc_url
                    post_headers["Content-Type"] = "application/x-www-form-urlencoded"
                    
                    async with session.post(post_url, data=post_data, headers=post_headers, proxy=proxy, allow_redirects=True) as post_resp:
                        post_html = await post_resp.text()
                        
                        # 1. Allaqachon ovoz berilgan holat
                        h2_match = re.search(r'<h2>(.*?)</h2>', post_html, re.DOTALL | re.I)
                        p_match = re.search(r'<p>(.*?)</p>', post_html, re.DOTALL | re.I)
                        raw_msg = ""
                        if h2_match:
                            raw_msg = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()
                        elif p_match:
                            raw_msg = re.sub(r'<[^>]+>', '', p_match.group(1)).strip()
                            
                        already_voted_terms = [
                            "аввал овоз берилган", "аввал берилган овоз", "номингизга расмийлаштирилган",
                            "ovoz qabul qilingan", "allaqachon", "муваффақиятли қабул қилинган",
                            "аввал берилган", "овоз берилган", "аввал овоз"
                        ]
                        if any(k in post_html.lower() for k in already_voted_terms):
                            detail_msg = raw_msg or "Ushbu fuqaro / raqam orqali bu mavsumda allaqachon ovoz berilgan."
                            logger.info(f"OpenBudget already_voted: {clean_phone} -> {detail_msg}")
                            return False, "already_voted", {"phone": clean_phone, "detail": detail_msg}
                            
                        # 2. Captcha mos kelmadi holati
                        if any(k in post_html.lower() for k in ["мос келмади", "mos kelmadi", "noto'g'ri"]):
                            logger.warning(f"MVC captcha mos kelmadi, qayta urinish {attempt+1}/4")
                            continue
                            
                        # 3. Muvaffaqiyat: OTP forma qaytgan holat
                        form_m = re.search(r"<form[^>]*action=\"([^\"]+)\"[^>]*>([\s\S]*?)</form>", post_html, re.I)
                        inputs = re.findall(r'<input[^>]+name=["\']([^"\']+)["\'][^>]*>', post_html, re.I)
                        
                        if form_m or any(i.lower() in ["otpcode", "smscode", "code"] for i in inputs) or post_resp.status in (200, 201, 302):
                            cookies_dict = {c.key: c.value for c in jar}
                            logger.info(f"Rasmiy MVC Ovoz SMS muvaffaqiyatli yuborildi: {clean_phone} -> {target_uuid}")
                            return True, "SMS tasdiqlash kodi yuborildi.", {
                                "flow": "mvc",
                                "phone": "998" + clean_phone,
                                "project_id": project_id,
                                "target_uuid": str(target_uuid),
                                "cookies": cookies_dict
                            }
            except Exception as e:
                logger.warning(f"MVC Vote SMS xatosi (urinish {attempt+1}): {e}")
                
        return False, "Ovoz berish xizmati band yoki captcha yechilmadi. Iltimos qaytadan urinib ko'ring.", None

    @classmethod
    async def send_registration_otp(
        cls,
        first_name: str,
        last_name: str,
        phone_number: str,
        gender: str,
        birth_date: str,
        region_id: int,
        district_id: int,
        project_id: str,
        captcha_key: str | None = None,
        captcha_result: int | None = None,
        profession: str = "Xodim"
    ) -> tuple[bool, str, dict | None]:
        """
        POST /v1/register/send-otp
        Yangi foydalanuvchini Open Budget tizimida ro'yxatdan o'tkazish uchun SMS OTP yuboradi.
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]

        # Mock rejim
        if settings.MOCK_OPENBUDGET:
            return True, "Ro'yxatdan o'tish SMS kodi yuborildi (Simulyatsiya: 1111)", {
                "phone": "998" + clean_phone,
                "project_id": project_id,
                "otp_key": "mock_reg_otp_key",
                "otp_code": "1111"
            }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
        }
        
        gender_code = "M" if str(gender).upper() in ("MALE", "M", "ERKAK") else "F"
        full_name_str = f"{first_name.strip()} {last_name.strip()}".strip() or "Fuqaro"
        
        reg_payload = {
            "captcha_key": captcha_key or "",
            "captcha_result": int(captcha_result) if captcha_result is not None else 0,
            "phone_number": "998" + clean_phone,
            "district_id": int(district_id),
            "fullname": full_name_str,
            "gender": gender_code,
            "birth_date": birth_date,
            "profession": profession or "Xodim",
            "region_id": int(region_id),
        }
        
        try:
            status, data, text = await cls._execute_request("POST", cls.register_send_otp_url(), headers=headers, json_data=reg_payload)
            logger.info(f"register/send-otp javob: {status} — {mask_sensitive_data(data)}")
            if status == 200:
                return True, "Ro'yxatdan o'tish SMS kodi yuborildi.", {
                    "phone": "998" + clean_phone,
                    "project_id": project_id,
                    "otp_key": data.get("otpKey"),
                }
            else:
                msg = data.get("message") or data.get("detail") or f"Status: {status}"
                return False, f"Ro'yxatdan o'tishda xatolik: {msg}", None
        except Exception as e:
            logger.error(f"Register send-otp exception: {e}")
            return False, "Ro'yxatdan o'tish tizimiga ulanib bo'lmadi.", None

    @classmethod
    async def verify_registration_otp(
        cls,
        phone_number: str,
        code: str,
        session_data: dict
    ) -> tuple[bool, str]:
        """
        POST /v1/register/verify-otp
        Ro'yxatdan o'tish SMS kodini tasdiqlab, hisobni faollashtiradi va access_token qaytaradi.
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = "998" + clean_phone

        # Mock rejim
        if settings.MOCK_OPENBUDGET:
            expected = session_data.get("otp_code", "1111")
            if code == expected:
                return True, "mock_access_token_registered"
            return False, "Kiritilgan SMS kod noto'g'ri."

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
        }
        
        verify_payload = {
            "phone_number": clean_phone,
            "otp_code": code.strip(),
            "otp_key": session_data.get("otp_key")
        }
        
        try:
            status, res_data, text = await cls._execute_request("POST", cls.register_verify_otp_url(), headers=headers, json_data=verify_payload)
            if status != 200:
                msg = res_data.get("message") or f"Status kodi: {status}"
                return False, f"SMS tasdiqlashda xatolik: {msg}"

            logger.info(f"register/verify-otp javob: {status} — {mask_sensitive_data(res_data)}")
            access_token = res_data.get("access_token")
            if not access_token:
                return False, "Tizimdan ruxsat tokenini olib bo'lmadi."
            return True, access_token
        except Exception as e:
            logger.error(f"Verify registration SMS Exception: {e}")
            return False, "SMS tasdiqlashda tarmoq xatoligi yuz berdi."

    @classmethod
    async def verify_sms_code(
        cls,
        phone_number: str,
        code: str,
        session_data: dict
    ) -> tuple[bool, str]:
        """
        SMS kodni portal orqali tasdiqlab login qiladi.
        Muvaffaqiyatli bo'lsa (True, access_token) qaytaradi.
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = "998" + clean_phone

        # --- MOCK REJIM ---
        if settings.MOCK_OPENBUDGET:
            expected = session_data.get("otp_code", "1111")
            if code == expected:
                return True, "mock_access_token"
            return False, "Kiritilgan SMS kod noto'g'ri. Qayta tekshiring."

        # --- REAL REJIM ---
        # 1. Agar MVC rasmiy loyiha oqimi bo'lsa
        if session_data and session_data.get("flow") == "mvc":
            cookies = session_data.get("cookies", {})
            target_uuid = session_data.get("target_uuid")
            
            verify_url = "https://openbudget.uz/api/v2/vote/mvc/verify"
            verify_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://openbudget.uz/api/v2/vote/mvc/captcha/{target_uuid}",
                "Origin": "https://openbudget.uz",
            }
            verify_data = {
                "otpCode": str(code).strip(),
                "grToken": ""
            }
            
            jar = aiohttp.CookieJar(unsafe=True)
            for k, v in cookies.items():
                jar.update_cookies({k: v}, urllib.parse.urlparse("https://openbudget.uz"))
                
            proxy = settings.PROXY_URL or None
            try:
                timeout = aiohttp.ClientTimeout(total=25)
                async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
                    async with session.post(verify_url, data=verify_data, headers=verify_headers, proxy=proxy) as resp:
                        v_html = await resp.text()
                        if resp.status == 200:
                            logger.info(f"Rasmiy MVC Ovoz muvaffaqiyatli qabul qilindi: {clean_phone} -> {target_uuid}")
                            return True, "mvc_voted"
                        else:
                            logger.warning(f"MVC Verify status: {resp.status} — {v_html[:200]}")
                            return False, "Kiritilgan SMS kod noto'g'ri yoki muddati tugagan."
            except Exception as e:
                logger.error(f"MVC Verify xatosi: {e}")
                return False, "SMS tasdiqlashda tarmoq xatoligi yuz berdi."

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
        }
        
        login_payload = {
            "phone_number": clean_phone,
            "otp_code": code,
            "otp_key": session_data.get("otp_key"),
        }
        
        try:
            status, login_data, text = await cls._execute_request("POST", cls.verify_otp_url(), headers=headers, json_data=login_payload)
            if status != 200:
                msg = login_data.get("message") or f"Status kodi: {status}"
                return False, f"SMS tasdiqlashda xatolik: {msg}"

            logger.info(f"verify-otp javob: {status} — {mask_sensitive_data(login_data)}")
            access_token = login_data.get("access_token")
            if not access_token:
                return False, "Tizimdan ruxsat tokenini olib bo'lmadi."
            return True, access_token

        except Exception as e:
            logger.error(f"Verify SMS Exception: {e}", exc_info=True)
            return False, "SMS tasdiqlash jarayonida tarmoq xatoligi yuz berdi."

    @classmethod
    async def cast_vote(
        cls,
        project_id: str,
        access_token: str,
        captcha_key: str,
        captcha_result: int
    ) -> tuple[bool, str]:
        """
        Olingan access_token va hal qilingan captcha kodi yordamida yakuniy ovozni rasmiylashtiradi.
        """
        # --- MOCK REJIM ---
        if settings.MOCK_OPENBUDGET:
            return True, "Sizning ovozingiz muvaffaqiyatli qabul qilindi!"

        # --- REAL REJIM ---
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
            "Authorization": access_token
        }
        
        # Agar project_id public_id bo'lsa (masalan 055529529012), uni haqiqiy UUID ga o'giramiz
        target_uuid = project_id
        if len(str(project_id)) == 12 and str(project_id).isdigit():
            try:
                init_info = await cls.find_initiative(str(project_id))
                if init_info and init_info.get("id"):
                    target_uuid = str(init_info.get("id"))
                    logger.info(f"Public ID {project_id} -> UUID {target_uuid} ga o'girildi")
            except Exception as e:
                logger.warning(f"Initiative UUID topishda xato: {e}")

        vote_payload = {
            "initiativeId": target_uuid,
            "captchaKey": captcha_key,
            "captchaResult": captcha_result
        }

        try:
            v_status, v_data, text = await cls._execute_request("POST", cls.cast_vote_url(), headers=headers, json_data=vote_payload)
            logger.info(f"Ovoz berish yakuniy javobi: {v_status} - {mask_sensitive_data(v_data)}")
            if v_status == 200:
                return True, "Sizning ovozingiz muvaffaqiyatli qabul qilindi!"
            else:
                detail = v_data.get("message") or v_data.get("detail") or f"Xatolik kodi: {v_status}"
                if "already" in detail.lower() or "ovoz berilgan" in detail.lower():
                    return False, "already_voted"
                if v_status in (410, 400) and ("captcha" in detail.lower() or "key" in detail.lower()):
                    return False, "invalid_captcha"
                return False, f"Ovoz berish rad etildi: {detail}"

        except Exception as e:
            logger.error(f"Cast Vote Exception: {e}", exc_info=True)
            return False, "Ovoz berish jarayonida tarmoq xatoligi yuz berdi."

    @classmethod
    async def get_boards(cls) -> list[dict]:
        """Barcha faol va arxivlangan taxtalar ro'yxatini yuklab oladi"""
        if settings.MOCK_OPENBUDGET:
            return [{"id": 55, "type": "INITIATIVE", "is_active": True, "title": "Mock Board (Tashabbusli Budjet)"}]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz"
        }
        try:
            status, data, text = await cls._execute_request("GET", cls._get_url("/v1/boards"), headers=headers)
            if status == 200:
                return data.get("boards", [])
        except Exception as e:
            logger.error(f"Boards yuklashda xatolik: {e}")
        return []

    @classmethod
    async def find_initiative(cls, project_id: str) -> dict | None:
        """
        Open Budget portalidan loyihani ID orqali qidirib topadi.
        Faol va boshqa INITIATIVE taxtalarini tekshiradi.
        """
        if settings.MOCK_OPENBUDGET:
            return {
                "boardId": 55,
                "categoryName": "Mock Kategoriya (Yo'llarni ta'mirlash)",
                "description": "Mock loyiha tavsifi. Ko'chamizga asfalt yotqizish zarur.",
                "districtName": "Koson tumani",
                "quarterName": "Xalqobod MFY",
                "regionName": "Qashqadaryo viloyati",
                "voteCount": 120,
                "id": "mock-uuid-12345-67890",
                "publicId": f"055{project_id}5005",
                "boardTitle": "Mock Board (Tashabbusli Budjet)"
            }

        boards = await cls.get_boards()
        initiative_boards = [b for b in boards if b.get("type") == "INITIATIVE"]
        
        # Boardlarni ID bo'yicha kamayish tartibida saralaymiz (oxirgisi birinchi)
        initiative_boards.sort(key=lambda x: x.get("id", 0), reverse=True)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz"
        }

        for board in initiative_boards:
            board_id = board.get("id")
            url = cls._get_url(f"/v2/info/board/{board_id}?search={project_id}")
            try:
                status, data, text = await cls._execute_request("GET", url, headers=headers, timeout_seconds=12)
                if status == 200:
                    content = data.get("content", [])
                    # Qidiruv natijalari orasidan publicId kiritilgan project_id ni o'z ichiga olganini topamiz
                    for item in content:
                        pub_id = str(item.get("publicId", ""))
                        expected_prefix = f"0{board_id}{project_id}"
                        if pub_id.startswith(expected_prefix) or project_id in pub_id:
                            item["boardTitle"] = board.get("title", "Tashabbusli Budjet")
                            return item
            except Exception as e:
                logger.error(f"Board {board_id} dan loyiha qidirishda xatolik: {e}")
                
        return None

    @classmethod
    async def get_official_votes_list(cls, project_id: str, page: int = 0, size: int = 50) -> list[dict]:
        """
        Open Budget portalining rasmiy 'Ovozlar ro'yxati' jadvalidan 
        berilgan barcha ovozlarni (maskalangan telefon va sana) yuklab oladi.
        """
        if settings.MOCK_OPENBUDGET:
            return []

        # 1. Agar project_id publicId bo'lsa (0555...), UUID ga o'giramiz
        target_uuid = project_id
        if len(str(project_id)) == 12 and str(project_id).isdigit():
            init_info = await cls.find_initiative(str(project_id))
            if init_info and init_info.get("id"):
                target_uuid = str(init_info.get("id"))

        # 2. Captcha yuklaymiz va yechamiz
        success_cap, cap_msg, cap_data = await cls.get_captcha()
        if not success_cap or not cap_data:
            return []

        captcha_key = cap_data.get("key")
        captcha_image = cap_data.get("image_base64")
        if not captcha_image:
            return []

        try:
            from services.captcha_solver import solve_captcha
            auto_res = await solve_captcha(captcha_image)
        except Exception:
            auto_res = None

        if auto_res is None:
            return []

        # 3. get-initiative-token olamiz
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz"
        }
        token_payload = {
            "initiativeId": target_uuid,
            "captchaKey": captcha_key,
            "captchaResult": auto_res
        }

        try:
            status, res_data, text = await cls._execute_request(
                "POST", 
                cls._get_url("/v2/info/get-initiative-token"), 
                headers=headers, 
                json_data=token_payload
            )
            if status != 200 or not res_data.get("token"):
                logger.warning(f"get-initiative-token olinmadi ({status}): {res_data}")
                return []
            
            init_token = res_data.get("token")

            # 4. /v2/info/votes/{init_token} orqali ovozlar ro'yxatini olamiz
            votes_url = cls._get_url(f"/v2/info/votes/{init_token}?page={page}&size={size}")
            v_status, v_data, v_text = await cls._execute_request(
                "GET", 
                votes_url, 
                headers=headers
            )
            if v_status == 200:
                content = v_data.get("content", [])
                logger.info(f"Open Budget 'Ovozlar ro'yxati'dan {len(content)} ta rasmiy ovoz yuklandi.")
                return content
        except Exception as e:
            logger.error(f"Ovozlar ro'yxatini yuklashda xato: {e}")
        return []


