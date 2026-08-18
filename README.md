# 🗳️ Open Budget Uzbekistan API Gateway & Telegram Bot Platform

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-Framework-009688?style=for-the-badge&logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram" alt="Aiogram 3" />
  <img src="https://img.shields.io/badge/PostgreSQL-Database-336791?style=for-the-badge&logo=postgresql" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/Telegram_Bot-@Budjetuz2026__Bot-26A5E4?style=for-the-badge&logo=telegram" alt="Telegram Bot" />
</p>

---

## 📌 Overview / Umumiy Ma'lumot

**Open Budget Uzbekistan API Gateway** — O'zbekistondagi **Open Budget (Tashabbusli Budjet - openbudget.uz)** platformasi uchun yagona, yuqori tezlikdagi va to'liq avtomatlashtirilgan rasmiy API Gateway va Telegram Bot ekotizimidir.

Ushbu servis dasturchilar, jamoalar va tashkilotlarga Open Budget tashabbuslariga ovoz yig'ish jarayonini (Captcha yechish, SMS yuborish, OTP tasdiqlash va ovozni hisoblash) to'liq avtomatlashtirish imkonini beradi.

* 🤖 **Rasmiy Telegram Bot**: [@Budjetuz2026_Bot](https://t.me/Budjetuz2026_Bot)
* 🌐 **API Gateway URL**: `https://openbudjet-production.up.railway.app/api/v1`
* 🤖 **AI / LLM Yo'riqnomasi**: `https://openbudjet-production.up.railway.app/llms.txt`

---

## ⚡ Asosiy Imkoniyatlar (Key Features)

1. **Avtomatlashtirilgan Captcha va SMS Gateway**:
   * Open Budget tizimidagi boshqotirma (puzzle captcha) va SMS OTP kodlarini real vaqtda qayta ishlash.
2. **Tezkor API Kalitlar**:
   * Telegram bot [@Budjetuz2026_Bot](https://t.me/Budjetuz2026_Bot) orqali avtomatik to'lov qilib, soniyalar ichida shaxsiy `ob_api_...` kalitini olish.
3. **Tayyor Mijoz Boti (Turnkey Client Bot)**:
   * Python (Aiogram 3) da yozilgan, ichki SQLite bazaga ega to'liq mustaqil bot kodi (`open_budget_client_bot.py`).
4. **Yuqori Xavfsizlik**:
   * HMAC-SHA256 imzolari, Anti-Flood (Throttling) himoyasi va doimiy monitoring.

---

## 🚀 Qadamma-qadam Ishga Tushirish (Quickstart)

### 1. API Kalit olish
1. Telegram'da [@Budjetuz2026_Bot](https://t.me/Budjetuz2026_Bot) botiga kiring.
2. **/start** bosing va **🤝 Hamkorlik & API** bo'limidan o'zingizga mos tarifni tanlab, API kalit sotib oling.

### 2. So'rov yuborish (cURL Misoli)

```bash
curl -X GET "https://openbudjet-production.up.railway.app/api/v1/initiative/32541" \
     -H "X-API-Key: ob_api_sizning_kalitingiz"
```

### 3. Python (Aiogram 3 / aiohttp) Misoli

```python
import aiohttp
import asyncio

API_BASE = "https://openbudjet-production.up.railway.app/api/v1"
API_KEY = "ob_api_your_key_here"  # @Budjetuz2026_Bot dan olingan kalit

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

async def check_initiative(project_id: str):
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{API_BASE}/initiative/{project_id}") as response:
            data = await response.json()
            print("Loyiha ma'lumotlari:", data)

if __name__ == "__main__":
    asyncio.run(check_initiative("32541"))
```

---

## 🛠️ API Endpointlari (API Reference)

| Metod | Endpoint | Tavsif |
|---|---|---|
| `GET` | `/api/v1/tariffs` | Ochiq API tariflari va narxlar ro'yxati |
| `GET` | `/api/v1/initiative/{id}` | Tashabbus nomi, viloyat va tuman ma'lumotlari |
| `GET` | `/api/v1/captcha` | SMS so'rash uchun Captcha rasmi (Base64) |
| `POST` | `/api/v1/send-otp` | Telefon raqamga OneID SMS kodini jo'natish |
| `POST` | `/api/v1/verify-otp` | SMS kodni tekshirish va access_token olish |
| `POST` | `/api/v1/cast-vote` | Yakuniy ovozni tasdiqlash va hisobga o'tkazish |

---

## 📞 Aloqa va Yordam

* 🤖 **Telegram Bot**: [@Budjetuz2026_Bot](https://t.me/Budjetuz2026_Bot)
* 💼 **Hamkorlik va Savollar**: Telegram botning `/admin` yoki qo'llab-quvvatlash bo'limi orqali.

---
<p align="center">Made with ❤️ for Open Budget Uzbekistan Community</p>
