from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import settings

# Railway yoki boshqa PostgreSQL ulanish havolasini asinxron drayverga moslashtirish
db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://") and not db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Async engine yaratish. 
# Agar SQLite bo'lsa, ba'zi bir konfiguratsiyalar (masalan, check_same_thread) moslashtirilishi mumkin.
connect_args = {}
if db_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_async_engine(
    db_url,
    echo=False,
    connect_args=connect_args
)

# Sessiyalar yaratuvchi async sessionmaker
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# FastAPI uchun db dependency
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
