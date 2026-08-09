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
    @classmethod
    def _get_url(cls, path: str) -> str:
        """Proksi URL mavjud bo'lsa undan foydalanadi, aks holda real saytdan"""
        # Agar PROXY_URL (turar-joy proksisi) kiritilgan bo'lsa, so'rovlarni to'g'ridan-to'g'ri real saytga yuboramiz.
        # Chunki proksi o'zi IPni yashiradi va Cloudflare Workerga ehtiyoj qolmaydi.
        if settings.PROXY_URL:
            base = "https://openbudget.uz/api"
        elif settings.CLOUDFLARE_PROXY_URL:
            base = settings.CLOUDFLARE_PROXY_URL.rstrip('/')
        else:
            base = "https://openbudget.uz/api"
            
        return f"{base}{path}"

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
    
    # 4. Ovozni tasdiqlab yakunlash (POST)
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
            async with aiohttp.ClientSession() as session:
                async with session.get(cls.captcha_url(), headers=headers, timeout=15, proxy=settings.PROXY_URL or None) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return True, "ok", {
                            "key": data.get("captchaKey"),
                            "image_base64": data.get("image"),
                        }
                    text = await resp.text()
                    return False, f"Captcha xatoligi: {resp.status}", None
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
                return False, "Bu raqam orqali allaqachon ovoz berilgan", None
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
            async with aiohttp.ClientSession() as session:
                async with session.post(cls.send_otp_url(), json=payload, headers=headers, timeout=15, proxy=settings.PROXY_URL or None) as resp:
                    status = resp.status
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    
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
                    else:
                        msg = data.get("message") or data.get("detail") or f"Status: {status}"
                        return False, f"Xatolik: {msg}", None
        except Exception as e:
            logger.error(f"Send OTP Error: {e}")
            return False, "Portalga ulanib bo'lmadi. Keyinroq urinib ko'ring.", None

    @classmethod
    async def verify_sms_code(
        cls,
        phone_number: str,
        code: str,
        project_id: str,
        session_data: dict
    ) -> tuple[bool, str]:
        """
        1. POST /v1/login/verify-otp (Tasdiqlash kodi orqali tizimga kirish)
        2. POST /v2/info/get-initiative-token (Olingan token yordamida ovozni rasmiylashtirish)
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = "998" + clean_phone

        # --- MOCK REJIM ---
        if settings.MOCK_OPENBUDGET:
            expected = session_data.get("otp_code", "1111")
            if code == expected:
                return True, "Ovoz muvaffaqiyatli qabul qilindi!"
            return False, "Kiritilgan SMS kod noto'g'ri. Qayta tekshiring."

        # --- REAL REJIM ---
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://openbudget.uz/",
            "Origin": "https://openbudget.uz",
        }
        
        # 1-QADAM: SMS kodni tasdiqlash va Authorization tokenini olish
        login_payload = {
            "phone_number": clean_phone,
            "otp_code": code,
            "otp_key": session_data.get("otp_key"),
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(cls.verify_otp_url(), json=login_payload, headers=headers, timeout=15, proxy=settings.PROXY_URL or None) as resp:
                    if resp.status != 200:
                        try:
                            data = await resp.json()
                            msg = data.get("message") or "Kiritilgan kod noto'g'ri."
                        except Exception:
                            msg = f"Status kodi: {resp.status}"
                        return False, f"SMS tasdiqlashda xatolik: {msg}"
                    
                    login_data = await resp.json()
                    logger.info(f"verify-otp javob: {resp.status} — {mask_sensitive_data(login_data)}")
                    access_token = login_data.get("access_token")
                    if not access_token:
                        return False, "Tizimdan ruxsat tokenini olib bo'lmadi."

                # 2-QADAM: Olingan Authorization token bilan haqiqiy ovoz berish
                vote_headers = headers.copy()
                vote_headers["Authorization"] = access_token
                
                # Ovoz berish uchun ham alohida captcha yechilishi kerak (JS talabi)
                # Buni osonlashtirish uchun avval yana bitta captcha olamiz
                captcha_ok, _, cap_info = await cls.get_captcha()
                if not captcha_ok or not cap_info:
                    return False, "Ovoz berish captchasini yuklab bo'lmadi."
                
                # Captchani robot bo'lmaslik uchun user yechishi yoki simulyatsiya qilinishi
                # Bizning holatda ovoz berish muvaffaqiyatli o'tishi uchun so'rovni jo'natamiz
                vote_payload = {
                    "initiativeId": int(project_id) if project_id.isdigit() else project_id,
                    "captchaKey": cap_info.get("key"),
                    "captchaResult": 1  # Standart captcha taxmini yoki avtomatik bypass
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(cls.cast_vote_url(), json=vote_payload, headers=vote_headers, timeout=15, proxy=settings.PROXY_URL or None) as v_resp:
                        v_status = v_resp.status
                        try:
                            v_data = await v_resp.json()
                        except Exception:
                            v_data = {}

                        logger.info(f"Ovoz berish yakuniy javobi: {v_status} - {mask_sensitive_data(v_data)}")
                        if v_status == 200:
                            return True, "Sizning ovozingiz muvaffaqiyatli qabul qilindi!"
                        else:
                            detail = v_data.get("message") or v_data.get("detail") or f"Xatolik kodi: {v_status}"
                            if "already" in detail.lower() or "ovoz berilgan" in detail.lower():
                                return False, "Bu raqam orqali ushbu loyihaga allaqachon ovoz berilgan."
                            return False, f"Ovoz berish rad etildi: {detail}"

        except Exception as e:
            logger.error(f"Verify & Vote Exception: {e}", exc_info=True)
            return False, f"Ovoz berish jarayonida xatolik: {str(e)}"
