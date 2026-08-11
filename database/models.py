import enum
from datetime import datetime
from sqlalchemy import BigInteger, String, Float, Integer, DateTime, ForeignKey, Enum as SQLEnum, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class VoteStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    ALREADY_VOTED = "ALREADY_VOTED"
    FAILED = "FAILED"

class WithdrawalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    invited_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=True)
    total_referrals: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ProjectSettings(Base):
    __tablename__ = "project_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referral_price: Mapped[float] = mapped_column(Float, default=0.0)
    voter_reward: Mapped[float] = mapped_column(Float, default=0.0)
    min_withdrawal: Mapped[float] = mapped_column(Float, default=0.0)
    channel_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    card_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payment_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)



class OpenBudgetProject(Base):
    __tablename__ = "openbudget_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    project_url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

class VotesHistory(Base):
    __tablename__ = "votes_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    project_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[VoteStatus] = mapped_column(SQLEnum(VoteStatus), default=VoteStatus.SUCCESS)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Withdrawals(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    card_number: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(SQLEnum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(500), unique=True, index=True, nullable=False)      # Shifrlangan kalit
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # Tezkor qidiruv uchun SHA256 xeshi
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)                      # Telegram ID
    balance_uzs: Mapped[int] = mapped_column(Integer, default=0)                                 # UZS Balans
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class APIKeyPurchase(Base):
    __tablename__ = "api_key_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tariff_name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_uzs: Mapped[int] = mapped_column(Integer, nullable=False)
    unique_price_uzs: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    votes_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    votes: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)




