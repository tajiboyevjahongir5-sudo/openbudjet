import asyncio
from database.session import async_session
from database.models import User
from sqlalchemy import select

async def main():
    async with async_session() as s:
        res = await s.execute(select(User.telegram_id, User.username, User.full_name))
        rows = res.fetchall()
        print("DATABASE USERS:")
        for r in rows:
            print(f"ID: {r[0]} | @{r[1]} | {r[2]}")

if __name__ == "__main__":
    asyncio.run(main())
