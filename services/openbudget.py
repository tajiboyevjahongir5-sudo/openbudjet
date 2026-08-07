import logging
import aiohttp
from config import settings

logger = logging.getLogger(__name__)

class OpenBudgetService:
    # Open Budget portalining taxminiy endpointlari
    BASE_URL = "https://all.openbudget.uz/api/v1"
    SEND_SMS_URL = f"{BASE_URL}/user/temp/vote/"
    VERIFY_SMS_URL = f"{BASE_URL}/user/temp/vote/verify/"

    @classmethod
    async def check_and_send_sms(
        cls, 
        phone_number: str, 
        project_id: str, 
        captcha_code: str | None = None
    ) -> tuple[bool, str, dict | None]:
        """
        Open Budget portaliga telefon va loyiha ID yuborib, SMS yuborishni so'raydi.
        Agar captcha yuborilmagan bo'lsa, 'captcha_required' qaytaradi.
        Qaytaradi: (muvaffaqiyatli, xabar_matni, sessiya_ma'lumotlari)
        """
        # Telefon raqamini tozalash (faqat raqamlar)
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = f"998{clean_phone}"

        # --- MOCK REJIM (Sinov va Simulyatsiya) ---
        if settings.MOCK_OPENBUDGET:
            logger.info(f"[MOCK] SMS so'rovi: {clean_phone} -> loyiha: {project_id}, captcha: {captcha_code}")
            
            # Agar raqam '99' bilan tugasa, "avval ovoz berilgan" deb qaytaradi
            if clean_phone.endswith("99"):
                return False, "Bu raqam orqali ushbu loyihaga allaqachon ovoz berilgan", None

            # Agar captcha hali yuborilmagan bo'lsa, uni talab qilamiz
            if captcha_code is None:
                session_data = {
                    "phone": clean_phone,
                    "project_id": project_id,
                    "mock_session_id": f"sess_{clean_phone[-4:]}"
                }
                return False, "captcha_required", session_data

            # Agar captcha yuborilgan bo'lsa, SMS muvaffaqiyatli ketdi deb javob beramiz
            session_data = {
                "phone": clean_phone,
                "project_id": project_id,
                "mock_session_id": f"sess_{clean_phone[-4:]}",
                "otp_code": "1111",  # Mock uchun standart tasdiqlash kodi
                "captcha_code": captcha_code
            }
            return True, "SMS kod muvaffaqiyatli yuborildi (Simulyatsiya kodi: 1111)", session_data

        # --- REAL REJIM (Portal API bilan aloqa) ---
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Agar portal captcha talab qilsa va bizda captcha_code yo'q bo'lsa, 
        # odatda avval portal API'dan captcha rasmini tortib olishimiz kerak bo'ladi.
        # Bu yerda biz real API so'rovini captcha kodi bilan yoki usiz shakllantiramiz.
        payload = {
            "phone": clean_phone,
            "project_id": project_id
        }
        
        if captcha_code:
            payload["captcha"] = captcha_code

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(cls.SEND_SMS_URL, json=payload, headers=headers, timeout=15) as response:
                    status_code = response.status
                    response_json = await response.json()

                    logger.info(f"OpenBudget Send SMS Response Code: {status_code}, Body: {response_json}")

                    # 1. Captcha talab etiladigan holat
                    if status_code == 200 and response_json.get("captcha_required") and not captcha_code:
                        # Portal botdan captcha yechishni so'rayapti
                        session_token = response_json.get("session") or response_json.get("token")
                        session_data = {
                            "phone": clean_phone,
                            "project_id": project_id,
                            "session_token": session_token
                        }
                        return False, "captcha_required", session_data

                    # 2. Muvaffaqiyatli SMS yuborilgan holat
                    elif status_code == 200:
                        session_token = response_json.get("session") or response_json.get("token")
                        session_data = {
                            "phone": clean_phone,
                            "project_id": project_id,
                            "session_token": session_token
                        }
                        return True, "SMS tasdiqlash kodi yuborildi.", session_data
                    
                    # 3. Allaqachon ovoz berilgan yoki boshqa xatoliklar
                    elif status_code == 400:
                        detail = response_json.get("detail", "") or response_json.get("message", "")
                        if "already" in detail.lower() or "ovoz berilgan" in detail.lower():
                            return False, "Bu raqam orqali ushbu loyihaga allaqachon ovoz berilgan", None
                        return False, f"Xatolik: {detail or 'Noto\'g\'ri so\'rov'}", None
                    
                    else:
                        detail = response_json.get("detail", "Noma'lum xatolik")
                        return False, f"Portal xatoligi (Kod: {status_code}): {detail}", None

        except aiohttp.ClientConnectorError as e:
            logger.error(f"Open Budget ulanish xatoligi: {e}")
            return False, "Open Budget portaliga ulanib bo'lmadi. Keyinroq qayta urunib ko'ring.", None
        except Exception as e:
            logger.error(f"SMS yuborishda kutilmagan xatolik: {e}", exc_info=True)
            return False, f"SMS yuborishda xatolik yuz berdi: {str(e)}", None

    @classmethod
    async def verify_sms_code(
        cls, 
        phone_number: str, 
        code: str, 
        project_id: str, 
        session_data: dict
    ) -> tuple[bool, str]:
        """
        SMS kodni portal orqali tasdiqlaydi.
        Qaytaradi: (muvaffaqiyatli, xabar_matni)
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        if not clean_phone.startswith("998"):
            clean_phone = f"998{clean_phone}"

        # --- MOCK REJIM ---
        if settings.MOCK_OPENBUDGET:
            logger.info(f"[MOCK] SMS kod tekshirish: {clean_phone} -> kod: {code}")
            expected_code = session_data.get("otp_code", "1111")
            if code == expected_code:
                return True, "Ovoz muvaffaqiyatli qabul qilindi!"
            else:
                return False, "Kiritilgan SMS kod noto'g'ri. Iltimos qayta tekshiring."

        # --- REAL REJIM ---
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        session_token = session_data.get("session_token")
        payload = {
            "phone": clean_phone,
            "project_id": project_id,
            "code": code,
            "session": session_token
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(cls.VERIFY_SMS_URL, json=payload, headers=headers, timeout=15) as response:
                    status_code = response.status
                    response_json = await response.json()

                    logger.info(f"OpenBudget Verify SMS Response Code: {status_code}, Body: {response_json}")

                    if status_code == 200 or response_json.get("status") == "success":
                        return True, "Sizning ovozingiz muvaffaqiyatli qabul qilindi!"
                    else:
                        detail = response_json.get("detail") or response_json.get("message") or "Kiritilgan kod noto'g'ri."
                        return False, f"Xatolik: {detail}"

        except aiohttp.ClientConnectorError as e:
            logger.error(f"Open Budget ulanish xatoligi (verify): {e}")
            return False, "Open Budget portaliga ulanib bo'lmadi. Ovozni tasdiqlash imkonsiz."
        except Exception as e:
            logger.error(f"Kodni tekshirishda kutilmagan xatolik: {e}", exc_info=True)
            return False, f"Kodni tekshirishda xatolik yuz berdi: {str(e)}"
