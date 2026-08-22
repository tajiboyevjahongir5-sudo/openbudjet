import logging
import aiohttp
import base64
import time
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
        timeout_seconds: int = 15
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

        kw: dict = {"headers": req_headers, "timeout": aiohttp.ClientTimeout(total=timeout_seconds)}
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
        Captcha rasmi va kalitini yuklab oladi.
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
        try:
            status, data, text = await cls._execute_request("GET", cls.captcha_url(), headers=headers)
            if status == 200:
                return True, "ok", {
                    "key": data.get("captchaKey"),
                    "image_base64": data.get("image"),
                }
            return False, f"Captcha xatoligi: {status}", None
        except Exception as e:
            logger.error(f"Captcha yuklashda xatolik: {e}")
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
            return False, "Portalga ulanib bo'lmadi. Keyinroq urinib ko'ring.", None

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
        
        vote_payload = {
            "initiativeId": int(project_id) if project_id.isdigit() else project_id,
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

        # Barcha boardlarda qidirib chiqamiz
        for board in initiative_boards:
            board_id = board.get("id")
            url = cls._get_url(f"/v2/info/board/{board_id}?search={project_id}")
            try:
                session = await cls._get_session()
                async with session.get(url, headers=headers, timeout=12, proxy=settings.PROXY_URL or None) as resp:
                    if resp.status == 200:
                        data = await resp.json()
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

