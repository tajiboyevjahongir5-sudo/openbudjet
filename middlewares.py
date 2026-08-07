import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

class ThrottlingMiddleware(BaseMiddleware):
    """
    Foydalanuvchilar botga juda tez (masalan, 1 sekunddan kam vaqt ichida) 
    so'rov yuborganda ularni bloklovchi Anti-flood Middleware.
    """
    def __init__(self, time_limit: float = 1.0) -> None:
        self.limit = time_limit
        self.last_request = {}  # {user_id: timestamp}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            user_id = user.id
            current_time = time.time()

            # Oxirgi so'rov vaqtini tekshiramiz
            if user_id in self.last_request:
                last_time = self.last_request[user_id]
                # Agar 1 soniyadan tezroq so'rov yuborgan bo'lsa
                if current_time - last_time < self.limit:
                    if isinstance(event, Message):
                        await event.answer("⚠️ Iltimos, xabarlarni juda tez-tez yubormang!")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⚠️ Juda tez so'rov yubordingiz. Biroz kuting!", show_alert=True)
                    return  # Handler ishga tushmaydi, so'rov rad etiladi

            # Yangi vaqtni saqlaymiz
            self.last_request[user_id] = current_time

            # Xotirani nazorat qilish (ro'yxat 10000 tadan oshganda eski ma'lumotlarni tozalaymiz)
            if len(self.last_request) > 10000:
                self.last_request = {k: v for k, v in self.last_request.items() if current_time - v < 5.0}

        return await handler(event, data)
