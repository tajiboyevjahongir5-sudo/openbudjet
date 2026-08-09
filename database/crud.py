from datetime import datetime
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, ProjectSettings, VotesHistory, Withdrawals, VoteStatus, WithdrawalStatus, OpenBudgetProject

# --- Foydalanuvchilar bilan ishlash ---

async def get_user(db: AsyncSession, telegram_id: int) -> User | None:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()

async def get_or_create_user(
    db: AsyncSession, 
    telegram_id: int, 
    username: str | None = None, 
    full_name: str | None = None,
    invited_by: int | None = None
) -> tuple[User, bool]:
    """
    Foydalanuvchini oladi yoki yangi yaratadi.
    Yangi yaratilgan bo'lsa True, aks holda False qaytaradi.
    """
    user = await get_user(db, telegram_id)
    if user:
        # Malumotlar yangilangan bo'lsa, bazada ham yangilab qo'yamiz
        updated = False
        if username and user.username != username:
            user.username = username
            updated = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            updated = True
        if updated:
            await db.commit()
        return user, False

    # Agar taklif qilgan odam bo'lsa va u o'zi bo'lmasa, hamda bazada mavjud bo'lsa
    valid_invited_by = None
    if invited_by and invited_by != telegram_id:
        referrer = await get_user(db, invited_by)
        if referrer:
            valid_invited_by = invited_by

    new_user = User(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        invited_by=valid_invited_by,
        balance=0.0,
        total_referrals=0,
        created_at=datetime.utcnow()
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user, True

async def add_user_balance(db: AsyncSession, telegram_id: int, amount: float) -> bool:
    """Foydalanuvchi balansiga pul qo'shadi"""
    user = await get_user(db, telegram_id)
    if user:
        user.balance += amount
        await db.commit()
        return True
    return False

# --- Loyiha sozlamalari bilan ishlash ---

async def get_project_settings(db: AsyncSession) -> ProjectSettings:
    """Joriy sozlamalarni qaytaradi. Agar bo'sh bo'lsa, default yaratadi."""
    result = await db.execute(select(ProjectSettings).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = ProjectSettings(
            referral_price=1500.0,
            voter_reward=1000.0,
            min_withdrawal=5000.0,
            channel_username=""
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings

async def update_project_settings(
    db: AsyncSession,
    referral_price: float | None = None,
    voter_reward: float | None = None,
    min_withdrawal: float | None = None,
    channel_username: str | None = None
) -> ProjectSettings:
    settings = await get_project_settings(db)
    if referral_price is not None:
        settings.referral_price = referral_price
    if voter_reward is not None:
        settings.voter_reward = voter_reward
    if min_withdrawal is not None:
        settings.min_withdrawal = min_withdrawal
    if channel_username is not None:
        settings.channel_username = channel_username
    await db.commit()
    await db.refresh(settings)
    return settings


# --- Ovozlar tarixi bilan ishlash ---

async def add_vote_history(
    db: AsyncSession,
    telegram_id: int,
    phone_number: str,
    project_id: str,
    status: VoteStatus
) -> VotesHistory:
    vote = VotesHistory(
        telegram_id=telegram_id,
        phone_number=phone_number,
        project_id=project_id,
        status=status,
        created_at=datetime.utcnow()
    )
    db.add(vote)
    await db.commit()
    await db.refresh(vote)
    return vote

async def check_phone_voted(db: AsyncSession, phone_number: str, project_id: str) -> bool:
    """Telefon raqam joriy loyihaga muvaffaqiyatli ovoz berganligini tekshiradi"""
    result = await db.execute(
        select(VotesHistory).where(
            VotesHistory.phone_number == phone_number,
            VotesHistory.project_id == project_id,
            VotesHistory.status == VoteStatus.SUCCESS
        )
    )
    return result.scalar_one_or_none() is not None

# --- Pul yechish (Withdrawals) operatsiyalari ---

async def create_withdrawal(
    db: AsyncSession,
    telegram_id: int,
    amount: float,
    card_number: str
) -> Withdrawals:
    # Avval userdan pulni yechib turamiz
    user = await get_user(db, telegram_id)
    if not user or user.balance < amount:
        raise ValueError("Balansda yetarli mablag' mavjud emas!")
    
    user.balance -= amount
    
    withdrawal = Withdrawals(
        telegram_id=telegram_id,
        amount=amount,
        card_number=card_number,
        status=WithdrawalStatus.PENDING,
        created_at=datetime.utcnow()
    )
    db.add(withdrawal)
    await db.commit()
    await db.refresh(withdrawal)
    return withdrawal

async def get_withdrawal(db: AsyncSession, withdrawal_id: int) -> Withdrawals | None:
    result = await db.execute(select(Withdrawals).where(Withdrawals.id == withdrawal_id))
    return result.scalar_one_or_none()

async def approve_withdrawal(db: AsyncSession, withdrawal_id: int) -> Withdrawals | None:
    withdrawal = await get_withdrawal(db, withdrawal_id)
    if withdrawal and withdrawal.status == WithdrawalStatus.PENDING:
        withdrawal.status = WithdrawalStatus.APPROVED
        await db.commit()
        await db.refresh(withdrawal)
        return withdrawal
    return None

async def reject_withdrawal(db: AsyncSession, withdrawal_id: int) -> Withdrawals | None:
    """
    Pul yechishni rad etadi va pulni foydalanuvchi balansiga qaytaradi.
    """
    withdrawal = await get_withdrawal(db, withdrawal_id)
    if withdrawal and withdrawal.status == WithdrawalStatus.PENDING:
        withdrawal.status = WithdrawalStatus.REJECTED
        
        # User balansiga qaytarib qo'shamiz
        user = await get_user(db, withdrawal.telegram_id)
        if user:
            user.balance += withdrawal.amount
            
        await db.commit()
        await db.refresh(withdrawal)
        return withdrawal
    return None

# --- Admin statistika operatsiyalari ---

async def get_admin_stats(db: AsyncSession, active_project_id: str) -> dict:
    # 1. Umumiy foydalanuvchilar soni
    total_users_result = await db.execute(select(func.count(User.telegram_id)))
    total_users = total_users_result.scalar_one() or 0

    # 2. Joriy loyihada to'plangan muvaffaqiyatli ovozlar soni
    current_votes_result = await db.execute(
        select(func.count(VotesHistory.id)).where(
            VotesHistory.project_id == active_project_id,
            VotesHistory.status == VoteStatus.SUCCESS
        )
    )
    current_votes = current_votes_result.scalar_one() or 0

    # 3. Tarixiy statistika: har bir loyiha bo'yicha muvaffaqiyatli ovozlar
    history_result = await db.execute(
        select(VotesHistory.project_id, func.count(VotesHistory.id))
        .where(VotesHistory.status == VoteStatus.SUCCESS)
        .group_by(VotesHistory.project_id)
    )
    history_stats = [{"project_id": row[0], "votes_count": row[1]} for row in history_result.all()]

    return {
        "total_users": total_users,
        "current_votes": current_votes,
        "history_stats": history_stats
    }

# --- Hisobot olish operatsiyalari ---

async def get_all_projects_with_votes(db: AsyncSession) -> list[str]:
    """Bazada muvaffaqiyatli ovozi bor barcha loyiha IDlarini qaytaradi"""
    result = await db.execute(
        select(VotesHistory.project_id)
        .where(VotesHistory.status == VoteStatus.SUCCESS)
        .distinct()
    )
    return [row[0] for row in result.all()]

async def get_votes_report(db: AsyncSession, project_id: str) -> list[dict]:
    """
    Loyiha bo'yicha muvaffaqiyatli ovoz berganlar ro'yxatini qaytaradi (ism, username, tel, sana).
    User jadvali bilan JOIN qilinadi.
    """
    # VotesHistory va User jadvallarini telegram_id bo'yicha JOIN qilamiz
    query = (
        select(
            User.full_name,
            User.username,
            VotesHistory.telegram_id,
            VotesHistory.phone_number,
            VotesHistory.created_at
        )
        .join(User, User.telegram_id == VotesHistory.telegram_id)
        .where(
            VotesHistory.project_id == project_id,
            VotesHistory.status == VoteStatus.SUCCESS
        )
        .order_by(VotesHistory.created_at.desc())
    )
    result = await db.execute(query)
    
    report_data = []
    for row in result.all():
        report_data.append({
            "full_name": row[0] or "Ism kiritilmagan",
            "username": f"@{row[1]}" if row[1] else "Mavjud emas",
            "telegram_id": row[2],
            "phone_number": row[3],
            "voted_at": row[4].strftime("%Y-%m-%d %H:%M:%S")
        })
    return report_data

async def get_all_user_ids(db: AsyncSession) -> list[int]:
    """Barcha foydalanuvchilarning telegram ID larini qaytaradi"""
    result = await db.execute(select(User.telegram_id))
    return [row[0] for row in result.all()]

# --- Open Budget Loyihalari bilan ishlash ---

async def get_active_project(db: AsyncSession) -> OpenBudgetProject | None:
    """Faol bo'lgan Open Budget loyihasini qaytaradi (faqat bittasi faol bo'la oladi)"""
    result = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.is_active == True))
    return result.scalar_one_or_none()

async def get_all_projects(db: AsyncSession) -> list[OpenBudgetProject]:
    """Barcha qo'shilgan loyihalar ro'yxatini qaytaradi"""
    result = await db.execute(select(OpenBudgetProject).order_by(OpenBudgetProject.id.asc()))
    return list(result.scalars().all())

async def add_project(db: AsyncSession, project_id: str, project_url: str) -> OpenBudgetProject:
    """Yangi loyiha qo'shadi. Agar birorta ham loyiha bo'lmasa, uni avtomat faol qiladi."""
    # Avval loyiha mavjudligini tekshiramiz
    existing = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.project_id == project_id))
    project = existing.scalar_one_or_none()
    if project:
        project.project_url = project_url
        await db.commit()
        await db.refresh(project)
        return project
        
    # Boshqa loyihalar borligini tekshiramiz
    all_projects = await get_all_projects(db)
    is_active = len(all_projects) == 0  # Agar birinchi loyiha bo'lsa, faol bo'ladi
    
    project = OpenBudgetProject(
        project_id=project_id,
        project_url=project_url,
        is_active=is_active
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

async def delete_project(db: AsyncSession, project_id: str) -> bool:
    """Loyihani o'chiradi. Agar o'chirilayotgan loyiha faol bo'lgan bo'lsa, boshqa biror loyihani faol qilmaydi."""
    result = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.project_id == project_id))
    project = result.scalar_one_or_none()
    if project:
        await db.delete(project)
        await db.commit()
        return True
    return False

async def activate_project(db: AsyncSession, project_id: str) -> OpenBudgetProject | None:
    """Tanlangan loyihani faollashtiradi va qolgan barcha loyihalarni faolsizlantiradi (Faqat 1ta faol cheklovi)"""
    # Avval barcha loyihalarni faolsizlantiramiz
    await db.execute(update(OpenBudgetProject).values(is_active=False))
    
    # Tanlanganini faollashtiramiz
    result = await db.execute(select(OpenBudgetProject).where(OpenBudgetProject.project_id == project_id))
    project = result.scalar_one_or_none()
    if project:
        project.is_active = True
        await db.commit()
        await db.refresh(project)
        return project
    else:
        await db.commit()
    return None

async def deactivate_all_projects(db: AsyncSession):
    """Barcha loyihalarni faolsizlantiradi"""
    await db.execute(update(OpenBudgetProject).values(is_active=False))
    await db.commit()
