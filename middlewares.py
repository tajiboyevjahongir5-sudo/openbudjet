import time
import asyncio
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit: float = 1.0) -> None:
        self.limit = time_limit
        self._data: dict[int, float] = {}
        self._cleanup_threshold = 5000

    def _cleanup(self, now: float) -> None:
        """Eskirgan yozuvlarni o'chiradi (O(n) lekin kamdan-kam chaqiriladi)"""
        self._data = {uid: ts for uid, ts in self._data.items() if now - ts < self.limit * 10}

    async def __call__(self, handler, event, data) -> Any:
        user = data.get("event_from_user")
        if user:
            now = time.time()
            uid = user.id
            last = self._data.get(uid, 0)
            if now - last < self.limit:
                if isinstance(event, Message):
                    await event.answer("⚠️ Iltimos, biroz kuting!")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Juda tez! Biroz kuting.", show_alert=True)
                return
            self._data[uid] = now
            if len(self._data) > self._cleanup_threshold:
                self._cleanup(now)
        return await handler(event, data)
