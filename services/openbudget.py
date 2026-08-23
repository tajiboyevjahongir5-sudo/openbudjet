import logging
import asyncio
import aiohttp
import base64
import time
import re
import json
import urllib.parse
from yarl import URL
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
    _mvc_sessions: dict[str, aiohttp.ClientSession] = {}
    _uuid_cache: dict[str, str] = {}

    @classmethod
    async def resolve_project_uuid(cls, project_id: str) -> str:
        if not project_id:
            return project_id
        if "-" in str(project_id):
            return str(project_id)
        if project_id in cls._uuid_cache:
            return cls._uuid_cache[project_id]
        
        info = await cls.find_initiative(project_id)
        if info and info.get("id"):
            uuid = str(info.get("id"))
            cls._uuid_cache[project_id] = uuid
            return uuid
        return project_id

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
        for s in cls._mvc_sessions.values():
            if not s.closed:
                await s.close()
        cls._mvc_sessions.clear()

    @classmethod
    def _get_url(cls, path: str) -> str:
        if settings.PROXY_URL:
            base = "https://openbudget.uz/api"
        elif settings.CLOUDFLARE_PROXY_URL:
            base = settings.CLOUDFLARE_PROXY_URL.rstrip('/')
        else:
            base = "https://openbudget.uz/api"
        return f"{base}{path}"

    @classmethod
    def _get_direct_url(cls, path: str) -> str:
        return f"https://openbudget.uz/api{path}"

    @classmethod
    def captcha_url(cls) -> str:
        return cls._get_url("/v2/vote/captcha-2")

    @classmethod
    def send_otp_url(cls) -> str:
        return cls._get_url("/v1/login/send-otp")

    @classmethod
    def verify_otp_url(cls) -> str:
        return cls._get_url("/v1/login/verify-otp")

    @classmethod
    def register_send_otp_url(cls) -> str:
        return cls._get_url("/v1/register/send-otp")

    @classmethod
    def register_verify_otp_url(cls) -> str:
        return cls._get_url("/v1/register/verify-otp")

    @classmethod
    def cast_vote_url(cls) -> str:
        return cls._get_url("/v2/info/get-initiative-token")

    @staticmethod
    def _access_captcha_token() -> str:
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
        session = await cls._get_session()

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
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]

        if captcha_key is None:
            return False, "captcha_required", {"phone": clean_phone, "project_id": project_id}

        if settings.MOCK_OPENBUDGET:
            if clean_phone.endswith("99"):
                return False, "Bu raqam orqali allaqachon ovoz berilgan", {"code": "already_voted"}
            if clean_phone.endswith("00"):
                return False, "not_registered", {"phone": "998" + clean_phone, "project_id": project_id}
            return True, "SMS kod yuborildi (Simulyatsiya kodi: 1111)", {
                "phone": "998" + clean_phone,
                "project_id": project_id,
                "otp_key": "mock_otp_key",
                "otp_code": "1111"
            }

        # Try registration flow first for extreme speed (like IshonchliOpenbudgetBot)
        import random
        uz_names = [
            "Jahongir Aliyev", "Sardor Karimov", "Madina Umarova", 
            "Diyorbek Toshpulatov", "Asadbek Rahimov", "Madina Toshmatova",
            "Zilola Ahmedova", "Azizbek Karimov", "Dilshodbek Ergashev"
        ]
        fullname = random.choice(uz_names)
        gender = random.choice(["M", "F"])
        year = random.randint(1985, 2004)
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        birth_date = f"{year:04d}-{month:02d}-{day:02d}"
        
        reg_ok, reg_msg, reg_session = await cls.send_registration_otp(
            first_name=fullname.split()[0],
            last_name=fullname.split()[1],
            phone_number="998" + clean_phone,
            gender=gender,
            birth_date=birth_date,
            region_id=1,
            district_id=1,
            project_id=project_id,
            captcha_key=captcha_key,
            captcha_result=captcha_result
        )
        if reg_ok and reg_session:
            reg_session["flow"] = "register"
            return True, "SMS tasdiqlash kodi yuborildi.", reg_session

        # If registration failed because user already exists, we fallback to MVC flow
        reg_msg_lower = reg_msg.lower() if reg_msg else ""
        if "ro'yxatdan o'tgan" in reg_msg_lower or "mavjud" in reg_msg_lower or "ro‘yxatdan" in reg_msg_lower or "registered" in reg_msg_lower:
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

                unregistered_keywords = [
                    "ro'yxatdan o'tmagan", "topilmadi", "not found", "not registered", "mavjud emas", "ro‘yxatdan", "foydalanuvchi",
                    "топилмади", "фойдаланувчи", "рўйхатдан", "маълумотлари топилмади", "топилмаган", "мавжуд эмас", "ҳеч қандай"
                ]
                if any(term in msg_lower for term in unregistered_keywords):
                    return False, "not_registered", {"phone": "998" + clean_phone, "project_id": project_id}

                already_voted_keywords = [
                    "ovoz bergan", "ovoz berilgan", "already voted", "boshqa raqam",
                    "овоз берган", "овоз берилган", "бошқа рақам"
                ]
                if any(term in msg_lower for term in already_voted_keywords):
                    return False, "already_voted", {"phone": clean_phone, "detail": msg}

                return False, f"Xatolik: {msg}", None
        except Exception as e:
            logger.error(f"Send OTP Error: {e}")
            return False, "SMS yuborishda tarmoq xatoligi yuz berdi.", None

    @classmethod
    async def send_mvc_initiative_sms(
        cls,
        phone_number: str,
        project_id: str
    ) -> tuple[bool, str, dict | None]:
        from services.captcha_solver import solve_mvc_visual_captcha
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]
            
        target_uuid = await cls.resolve_project_uuid(project_id)
                
        formatted_phone = f"{clean_phone[:2]} {clean_phone[2:5]}-{clean_phone[5:7]}-{clean_phone[7:]}"
        mvc_url = f"https://openbudget.uz/api/v2/vote/mvc/captcha/{target_uuid}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": "https://openbudget.uz",
            "Referer": f"https://openbudget.uz/initiative/{project_id}",
        }
        
        proxy = settings.PROXY_URL or None
        
        for attempt in range(2):
            try:
                jar = aiohttp.CookieJar(unsafe=True)
                timeout = aiohttp.ClientTimeout(total=30)
                session = aiohttp.ClientSession(cookie_jar=jar, timeout=timeout)
                
                async with session.get(mvc_url, headers=headers, proxy=proxy) as resp:
                    if resp.status != 200:
                        await session.close()
                        continue
                    html = await resp.text()
                    
                srcs = re.findall(r'<img[^>]+src="(data:image/[^"]+)"', html)
                if len(srcs) < 2:
                    srcs = re.findall(r"src='(data:image/[^']+)'", html)
                if len(srcs) < 2:
                    await session.close()
                    continue
                    
                img_a_b64 = srcs[0].split(",")[-1]
                img_b_b64 = srcs[1].split(",")[-1]
                
                points = await solve_mvc_visual_captcha(img_a_b64, img_b_b64)
                if not points or len(points) < 2:
                    await session.close()
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
                    
                    h2_match = re.search(r'<h2>(.*?)</h2>', post_html, re.DOTALL | re.I)
                    p_match = re.search(r'<p>(.*?)</p>', post_html, re.DOTALL | re.I)
                    raw_msg = ""
                    if h2_match:
                        raw_msg = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()
                    elif p_match:
                        raw_msg = re.sub(r'<[^>]+>', '', p_match.group(1)).strip()

                    already_voted_terms = ["аввал овоз берилган", "ovoz qabul qilingan", "allaqachon", "аввал берилган", "овоз берилган"]
                    if any(k in post_html.lower() for k in already_voted_terms):
                        await session.close()
                        detail_msg = raw_msg or "Ushbu fuqaro / raqam orqali bu mavsumda allaqachon ovoz berilgan."
                        return False, "already_voted", {"phone": clean_phone, "detail": detail_msg}
                        
                    if any(k in post_html.lower() for k in ["мос келмади", "mos kelmadi", "noto'g'ri"]):
                        await session.close()
                        continue
                        
                    form_m = re.search(r"<form[^>]*action=\"([^\"]+)\"[^>]*>([\s\S]*?)</form>", post_html, re.I)
                    inputs = re.findall(r'<input[^>]+name=["\']([^"\']+)["\'][^>]*>', post_html, re.I)
                    
                    if form_m or any(i.lower() in ["otpcode", "smscode", "code"] for i in inputs) or post_resp.status in (200, 201, 302):
                        session_key = f"998{clean_phone}"
                        if session_key in cls._mvc_sessions and not cls._mvc_sessions[session_key].closed:
                            await cls._mvc_sessions[session_key].close()
                        cls._mvc_sessions[session_key] = session

                        return True, "SMS tasdiqlash kodi yuborildi.", {
                            "flow": "mvc",
                            "phone": "998" + clean_phone,
                            "project_id": project_id,
                            "target_uuid": str(target_uuid),
                            "session_key": session_key
                        }
                    else:
                        await session.close()
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
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if clean_phone.startswith("998"):
            clean_phone = clean_phone[3:]

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
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = "998" + clean_phone

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
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = "998" + clean_phone

        if settings.MOCK_OPENBUDGET:
            expected = session_data.get("otp_code", "1111")
            if code == expected:
                return True, "mock_access_token"
            return False, "Kiritilgan SMS kod noto'g'ri. Qayta tekshiring."

        if session_data and session_data.get("flow") == "register":
            reg_ok, reg_token = await cls.verify_registration_otp(
                phone_number=clean_phone,
                code=code,
                session_data=session_data
            )
            if not reg_ok:
                return False, reg_token

            success_cap, cap_msg, cap_data = await cls.get_captcha()
            if not success_cap or not cap_data:
                return False, "Ovoz berish uchun kapcha yuklab bo'lmadi."

            from services.captcha_solver import solve_captcha_with_gemini
            solved_result = await solve_captcha_with_gemini(cap_data.get("image_base64"))
            if solved_result is None:
                return False, "Ovoz berish uchun kapcha yechib bo'lmadi."

            vote_success, vote_msg = await cls.cast_vote(
                project_id=session_data.get("project_id"),
                access_token=reg_token,
                captcha_key=cap_data.get("key"),
                captcha_result=int(solved_result)
            )
            return vote_success, vote_msg

        if session_data and session_data.get("flow") == "mvc":
            session_key = session_data.get("session_key") or clean_phone
            session = cls._mvc_sessions.get(session_key)

            if not session or session.closed:
                return False, "Seans eskirgan. Iltimos jarayonni bekor qilib, qaytadan SMS so'rang."

            target_uuid = session_data.get("target_uuid") or session_data.get("project_id")
            referer_url = f"https://openbudget.uz/api/v2/vote/mvc/captcha/{target_uuid}"
            page_url = "https://openbudget.uz/"

            recaptcha_token = ""

            verify_url = "https://openbudget.uz/api/v2/vote/mvc/verify"
            verify_data = {
                "otpCode": str(code).strip(),
                "grToken": recaptcha_token
            }
            verify_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": referer_url,
                "Origin": "https://openbudget.uz",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            
            proxy_url = settings.PROXY_URL or None
            
            try:
                async with session.post(verify_url, data=verify_data, headers=verify_headers, proxy=proxy_url, allow_redirects=True) as resp:
                    v_html = await resp.text()
                    v_lower = v_html.lower()
                    
                    success_words = ["табриклаймиз", "муваффақият", "қабул қилинди", "раҳмат", "muvaffaqiyat", "qabul qilindi"]
                    has_success = any(w in v_lower for w in success_words)
                    has_otp_form = bool("<form" in v_lower and ("otpcode" in v_lower or "verify" in v_lower))
                    has_danger = bool(re.search(r'class="[^"]*text-danger[^"]*"', v_html, re.I)) or "код хато" in v_lower or "xato" in v_lower
                    
                    if has_success and not has_otp_form and not has_danger:
                        await session.close()
                        cls._mvc_sessions.pop(session_key, None)
                        return True, "mvc_voted"
                    else:
                        err_text = "Kiritilgan SMS kod noto'g'ri yoki muddati tugagan."
                        err_match = re.search(r'<p[^>]*class="[^"]*text-danger[^"]*"[^>]*>(.*?)</p>', v_html, re.I)
                        if err_match:
                            err_text = re.sub(r'<[^>]+>', '', err_match.group(1)).strip()
                        return False, err_text
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
            "otp_key": session_data.get("otp_key") if session_data else None,
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
        if settings.MOCK_OPENBUDGET:
            return True, "Sizning ovozingiz muvaffaqiyatli qabul qilindi!"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
            "Authorization": access_token
        }
        
        target_uuid = await cls.resolve_project_uuid(project_id)

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
        if settings.MOCK_OPENBUDGET:
            return [{"id": 55, "type": "INITIATIVE", "is_active": True, "title": "Mock Board"}]

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
        if settings.MOCK_OPENBUDGET:
            return {
                "boardId": 55,
                "categoryName": "Mock Kategoriya",
                "description": "Mock loyiha tavsifi",
                "voteCount": 120,
                "id": "mock-uuid-12345-67890",
                "publicId": f"055{project_id}5005",
                "boardTitle": "Mock Board"
            }

        boards = await cls.get_boards()
        initiative_boards = [b for b in boards if b.get("type") == "INITIATIVE"]
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
        if settings.MOCK_OPENBUDGET:
            return []

        target_uuid = await cls.resolve_project_uuid(project_id)

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
                return []
            
            init_token = res_data.get("token")
            votes_url = cls._get_url(f"/v2/info/votes/{init_token}?page={page}&size={size}")
            v_status, v_data, v_text = await cls._execute_request("GET", votes_url, headers=headers)
            if v_status == 200:
                return v_data.get("content", [])
        except Exception as e:
            logger.error(f"Ovozlar ro'yxatini yuklashda xato: {e}")
        return []
