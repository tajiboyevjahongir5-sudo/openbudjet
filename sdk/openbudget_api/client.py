import aiohttp
from typing import Optional, Dict, Any

class OpenBudgetClient:
    """
    Open Budget (openbudget.uz) API Gateway Asinxron Klienti.
    
    API kalit olish uchun:
    Telegram Bot: https://t.me/Budjetuz2026_Bot
    """
    
    def __init__(self, api_key: str, base_url: str = "https://openbudjet-production.up.railway.app/api/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    async def get_tariffs(self) -> Dict[str, Any]:
        """Barcha ochiq API tariflarini olish"""
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(f"{self.base_url}/tariffs") as resp:
                return await resp.json()

    async def get_initiative(self, project_id: str) -> Dict[str, Any]:
        """Tashabbus (Loyiha) ma'lumotlarini olish"""
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(f"{self.base_url}/initiative/{project_id}") as resp:
                return await resp.json()

    async def get_captcha(self) -> Dict[str, Any]:
        """Ovoz berish uchun Captcha rasmini olish"""
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(f"{self.base_url}/captcha") as resp:
                return await resp.json()

    async def send_otp(self, phone_number: str, project_id: str, captcha_key: str, captcha_result: int) -> Dict[str, Any]:
        """Telefon raqamiga SMS kod yuborish"""
        payload = {
            "phone_number": phone_number,
            "project_id": project_id,
            "captcha_key": captcha_key,
            "captcha_result": captcha_result
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.post(f"{self.base_url}/send-otp", json=payload) as resp:
                return await resp.json()

    async def verify_otp(self, phone_number: str, sms_code: str, session_token: str) -> Dict[str, Any]:
        """SMS kodni tekshirish"""
        payload = {
            "phone_number": phone_number,
            "sms_code": sms_code,
            "session_token": session_token
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.post(f"{self.base_url}/verify-otp", json=payload) as resp:
                return await resp.json()

    async def cast_vote(self, project_id: str, access_token: str, captcha_key: str, captcha_result: int) -> Dict[str, Any]:
        """Yakuniy ovozni tasdiqlash"""
        payload = {
            "project_id": project_id,
            "access_token": access_token,
            "captcha_key": captcha_key,
            "captcha_result": captcha_result
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.post(f"{self.base_url}/cast-vote", json=payload) as resp:
                return await resp.json()
