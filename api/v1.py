import asyncio
import hashlib
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from database.session import get_db
from database.models import APIKey, VoteStatus
from database import crud
from utils.api_auth import get_api_key
from services.openbudget import OpenBudgetService

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Pydantic So'rov Modellari ---

class OTPRequest(BaseModel):
    phone_number: str = Field(..., description="Telefon raqami (masalan: 998901234567)")
    captcha_key: str = Field(..., description="Captcha kaliti (captcha olishda qaytariladi)")
    captcha_result: int = Field(..., description="Captcha yechimi (rasmdagi raqamlar)")
    project_id: str = Field(..., description="Loyiha ID raqami yoki UUID kaliti")

class VerifyRequest(BaseModel):
    phone_number: str = Field(..., description="Telefon raqami (masalan: 998901234567)")
    otp_code: str = Field(..., description="Telefonga kelgan 6 xonali SMS kod")
    otp_key: str = Field(..., description="SMS yuborishda qaytarilgan otp_key")
    session_key: str | None = Field(None, description="Sessiya kaliti")
    flow: str | None = Field(None, description="Oqim turi (masalan, mvc)")
    session_data: dict | None = Field(None, description="Sessiya ma'lumotlari")

class RegisterOTPRequest(BaseModel):
    first_name: str = Field(..., description="Ism")
    last_name: str = Field("", description="Familiya")
    phone_number: str = Field(..., description="Telefon raqami (masalan: 998901234567)")
    gender: str = Field("MALE", description="Jinsi (MALE yoki FEMALE)")
    birth_date: str = Field("1998-01-01", description="Tug'ilgan sana (YYYY-MM-DD)")
    region_id: int = Field(..., description="Viloyat ID raqami")
    district_id: int = Field(..., description="Tuman ID raqami")
    project_id: str = Field(..., description="Loyiha ID raqami")
    captcha_key: str = Field("", description="Captcha kaliti")
    captcha_result: int = Field(0, description="Captcha natijasi")
    profession: str = Field("Xodim", description="Kasbi")

class RegisterVerifyRequest(BaseModel):
    phone_number: str = Field(..., description="Telefon raqami (masalan: 998901234567)")
    otp_code: str = Field(..., description="Telefonga kelgan 6 xonali SMS kod")
    otp_key: str = Field(..., description="Ro'yxatdan o'tishda qaytarilgan otp_key")

class VoteRequest(BaseModel):
    project_id: str = Field(..., description="Loyiha ID raqami yoki UUID kaliti")
    access_token: str = Field(..., description="Verify-OTP bosqichida olingan login tokeni")
    captcha_key: str = Field(..., description="2-captcha kaliti")
    captcha_result: int = Field(..., description="2-captcha yechimi (rasmdagi raqamlar)")
    phone_number: str | None = Field(None, description="Ovoz berilgan telefon raqami (bazaga yozish uchun)")


# --- API Yo'llari (Endpoints) ---

@router.post("/generate-trial-key")
async def generate_trial_key(db: AsyncSession = Depends(get_db)):
    """Mijozlarga test qilish uchun 50,000 so'm balansli trial API kalit yaratadi"""
    import secrets
    plain_key = f"ob_api_{secrets.token_hex(16)}"
    await crud.create_api_key(
        db=db,
        plain_key=plain_key,
        owner_id=0,
        initial_balance=50000
    )
    return {
        "status": "success",
        "api_key": plain_key,
        "balance_uzs": 50000,
        "base_url": "https://openbudjet-production.up.railway.app/api/v1",
        "docs": "https://openbudjet-production.up.railway.app/llms.txt"
    }

@router.get("/reset-vote/{phone}")
@router.delete("/reset-vote/{phone}")
async def reset_vote_endpoint(
    phone: str,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Test uchun telefon raqamining barcha ovozlar tarixini bazadan tozalaydi"""
    from database.models import VoteHistory
    from sqlalchemy import delete
    clean_p = "".join(filter(str.isdigit, phone))
    last_digits = clean_p[-9:] if len(clean_p) >= 9 else clean_p
    stmt = delete(VoteHistory).where(VoteHistory.phone_number.like(f"%{last_digits}%"))
    res = await db.execute(stmt)
    await db.commit()
    return {"status": "success", "message": f"{phone} raqami bazadan to'liq o'chirildi.", "deleted_count": res.rowcount}


@router.get("/boards")
async def get_boards(
    api_key: APIKey = Depends(get_api_key)
):
    """
    Open Budget faol va arxivlangan mavsumlari (boardlari) ro'yxatini yuklaydi.
    Narxi: Bepul
    """
    boards = await OpenBudgetService.get_boards()
    if not boards:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mavsumlar ro'yxatini yuklab bo'lmadi."
        )
    return {"status": "success", "boards": boards}


@router.get("/initiative/{project_id}")
@router.get("/project/{project_id}")
async def get_initiative_info(
    project_id: str
):
    """
    Kiritilgan ID (raqamli ID bo'yicha loyiha ma'lumotlarini qidirib topadi.
    Narxi: Bepul
    """
    initiative = await OpenBudgetService.find_initiative(project_id)
    if not initiative:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Loyiha topilmadi: {project_id}"
        )
    return {"status": "success", "initiative": initiative}


@router.post("/captcha")
async def get_captcha(
    api_key: APIKey = Depends(get_api_key)
):
    """
    Tizimdan yangi Captcha rasmi (base64 formatida) va uning kalitini yuklab oladi.
    Narxi: Bepul
    """
    success, msg, data = await OpenBudgetService.get_captcha()
    if not success or not data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Captcha yuklashda xatolik: {msg}"
        )
    
    # Gemini orqali avtomatik yechish
    image_base64 = data.get("image_base64")
    if image_base64 and not data.get("mock"):
        try:
            from services.captcha_solver import solve_captcha
            auto_result = await solve_captcha(image_base64)
            if auto_result is not None:
                data["solved_result"] = auto_result
        except Exception as e:
            logger.warning(f"API Captcha auto-solve error: {e}")

    return {"status": "success", "captcha": data}


@router.get("/check-phone")
async def check_phone_status(
    phone_number: str,
    project_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key)
):
    """
    Telefon raqami muayyan loyihaga bazada ovoz bergan-bermaganini tekshiradi.
    """
    clean_phone = "".join(filter(str.isdigit, phone_number))
    already_voted = await crud.check_phone_voted(db, clean_phone, project_id)
    return {
        "status": "success",
        "phone_number": clean_phone,
        "project_id": project_id,
        "already_voted": already_voted
    }


@router.post("/send-otp")
async def send_otp(
    req: OTPRequest,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Kiritilgan telefon raqam va captcha natijasi asosida SMS tasdiqlash kodini yuboradi.
    Narxi: Bepul
    """
    # Global bazani tekshiramiz: bu raqam allaqachon ovoz berganmi?
    clean_phone = "".join(filter(str.isdigit, req.phone_number))
    already_voted = await crud.check_phone_voted(db, clean_phone, req.project_id)
    if already_voted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="already_voted"
        )

    success, msg, session_data = await OpenBudgetService.check_and_send_sms(
        phone_number=req.phone_number,
        project_id=req.project_id,
        captcha_key=req.captcha_key,
        captcha_result=req.captcha_result
    )
    
    if not success:
        # Agar raqam ro'yxatdan o'tmagan bo'lsa yoki boshqa xato bo'lsa
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg
        )
        
    return {
        "status": "success",
        "message": "SMS kod muvaffaqiyatli yuborildi.",
        "otp_key": session_data.get("otp_key") if session_data else None,
        "session_key": session_data.get("session_key") if session_data else None,
        "flow": session_data.get("flow") if session_data else None,
        "session_data": session_data
    }
@router.post("/update-activity")
async def update_activity_endpoint():
    """
    Foydalanuvchi faolligini bildiradi, fondagi MVC hovuzi ishchisini uyg'otadi.
    """
    OpenBudgetService.update_activity()
    return {"status": "success"}



@router.get("/regions")
async def get_regions_and_districts():
    """
    Open Budget tizimi uchun barcha 14 ta viloyat va tumanlar ro'yxati.
    Narxi: Bepul
    """
    from utils.regions import REGIONS, DISTRICTS
    return {
        "status": "success",
        "regions": REGIONS,
        "districts": DISTRICTS
    }


@router.post("/register/send-otp")
async def register_send_otp(
    req: RegisterOTPRequest,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Open Budget'da ro'yxatdan o'tmagan yangi fuqaro uchun SMS OTP yuborish.
    Narxi: Bepul
    """
    # Global bazani tekshiramiz: bu raqam allaqachon ovoz berganmi?
    clean_phone = "".join(filter(str.isdigit, req.phone_number))
    already_voted = await crud.check_phone_voted(db, clean_phone, req.project_id)
    if already_voted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="already_voted"
        )

    success, msg, session_data = await OpenBudgetService.send_registration_otp(
        first_name=req.first_name,
        last_name=req.last_name,
        phone_number=req.phone_number,
        gender=req.gender,
        birth_date=req.birth_date,
        region_id=req.region_id,
        district_id=req.district_id,
        project_id=req.project_id,
        captcha_key=req.captcha_key,
        captcha_result=req.captcha_result,
        profession=req.profession
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg
        )
        
    return {
        "status": "success",
        "message": "Ro'yxatdan o'tish SMS kodi yuborildi.",
        "otp_key": session_data.get("otp_key") if session_data else None
    }


@router.post("/register/verify-otp")
async def register_verify_otp(
    req: RegisterVerifyRequest,
    api_key: APIKey = Depends(get_api_key)
):
    """
    Ro'yxatdan o'tish SMS kodini tasdiqlab, login access_token qaytaradi.
    Narxi: Bepul
    """
    session_data = {
        "otp_key": req.otp_key,
        "phone": req.phone_number
    }
    
    success, result_msg = await OpenBudgetService.verify_registration_otp(
        phone_number=req.phone_number,
        code=req.otp_code,
        session_data=session_data
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result_msg
        )
        
    return {
        "status": "success",
        "message": "Ro'yxatdan o'tish muvaffaqiyatli yakunlandi.",
        "access_token": result_msg
    }


@router.post("/verify-otp")
@router.post("/vote")
async def verify_otp(
    req: VerifyRequest,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Kiritilgan SMS kodni tekshiradi va muvaffaqiyatli bo'lsa login tokenini qaytaradi.
    Narxi: Bepul
    """
    s_key = req.session_key
    s_flow = req.flow
    if req.session_data and isinstance(req.session_data, dict):
        s_key = s_key or req.session_data.get("session_key")
        s_flow = s_flow or req.session_data.get("flow")

    # Botdan kelgan to'liq session_data ni olamiz, kerakli maydonlarni ustiga yozamiz
    session_data = dict(req.session_data) if req.session_data else {}
    session_data["otp_key"] = req.otp_key
    session_data["phone"] = req.phone_number
    session_data["session_key"] = s_key
    session_data["flow"] = s_flow or session_data.get("flow")
    logger.info(f"verify-otp session_data: {session_data}")
    
    success, result_msg = await OpenBudgetService.verify_sms_code(
        phone_number=req.phone_number,
        code=req.otp_code,
        session_data=session_data
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result_msg
        )
        
    # Agarda MVC yoki Register flow bo'lsa, ovoz tasdiqlanganda avtomatik yoziladi
    if result_msg == "mvc_voted":
        try:
            clean_phone = "".join(filter(str.isdigit, req.phone_number))
            await crud.add_vote_history(
                db=db,
                telegram_id=api_key.owner_id or 0,
                phone_number=clean_phone,
                project_id=session_data.get("project_id") or "",
                status=VoteStatus.SUCCESS,
            )
        except Exception as e:
            logger.error(f"MVC vote tarixini yozishda xatolik: {e}")

    return {
        "status": "success",
        "message": "SMS tasdiqlandi.",
        "access_token": result_msg  # verify_sms_code muvaffaqiyatli bo'lsa token qaytaradi
    }


@router.post("/cast-vote")
async def cast_vote(
    req: VoteRequest,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Olingan login tokeni va 2-captcha natijasi yordamida yakuniy ovozni rasmiylashtiradi.
    Narxi: 15 kunlik obuna asosida cheksiz ovoz berish.
    """
    # 1. Ovoz berish so'rovini yuboramiz
    try:
        success, result_msg = await OpenBudgetService.cast_vote(
            project_id=req.project_id,
            access_token=req.access_token,
            captcha_key=req.captcha_key,
            captcha_result=req.captcha_result
        )
    except Exception as e:
        logger.error(f"cast_vote kutilmagan xatolik: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tashqi OpenBudget serveri bilan ulanishda xatolik yuz berdi."
        )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result_msg
        )

    # 3. Ovoz tarixini asosiy bazaga muvaffaqiyatli deb yozib qo'yamiz (global cheklov uchun)
    if req.phone_number:
        try:
            clean_phone = "".join(filter(str.isdigit, req.phone_number))
            await crud.add_vote_history(
                db=db,
                telegram_id=api_key.owner_id or 0,
                phone_number=clean_phone,
                project_id=req.project_id,
                status=VoteStatus.SUCCESS,
                commit=True
            )
        except Exception as e:
            logger.error(f"API cast_vote history yozishda xatolik: {e}")
        
    return {
        "status": "success",
        "message": "Ovoz muvaffaqiyatli qabul qilindi!",
        "detail": result_msg
    }


# --- API Kalit Sotib Olish (Tijorat va Integratsiya) ---

class BuyKeyRequest(BaseModel):
    telegram_id: int = Field(..., description="Xaridorning Telegram ID raqami")
    votes: int = Field(..., description="Tanlangan tarifdagi ovozlar soni")
    target_key: str | None = Field(None, description="Balansi to'ldiriladigan mavjud API kalit")


@router.get("/tariffs")
async def get_tariffs_public(db: AsyncSession = Depends(get_db)):
    """
    Barcha mavjud API kalit tariflari ro'yxatini qaytaradi (Mijoz boti orqali ko'rish uchun).
    """
    tariffs = await crud.get_all_tariffs(db)
    return {
        "status": "success",
        "tariffs": [
            {
                "id": t.id,
                "name": t.name,
                "votes": t.votes,
                "price": t.price
            }
            for t in tariffs
        ]
    }


@router.get("/key-info")
async def get_key_info(
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db)
):
    """
    API kalit holati, balansi, yaratilgan sanasi va qolgan muddatini qaytaradi.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key talab qilinadi")
    
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    api_key = await crud.get_api_key_by_hash(db, key_hash)
    if not api_key:
        raise HTTPException(status_code=401, detail="Ushbu API kalit yaroqsiz.")
    
    from sqlalchemy import select, func
    from database.models import APIKeyPurchase
    
    result = await db.execute(
        select(func.sum(APIKeyPurchase.price_uzs))
        .where(
            APIKeyPurchase.generated_key == x_api_key,
            APIKeyPurchase.status == "COMPLETED"
        )
    )
    row = result.first()
    total_paid_uzs = (row[0] if row and row[0] else 500000)
    
    created_at_str = api_key.created_at.strftime("%d.%m.%Y, %H:%M") if api_key.created_at else "—"
    activated_str = api_key.activated_at.strftime("%d.%m.%Y, %H:%M") if api_key.activated_at else "Faollashtirilmagan"
    expires_str = api_key.expires_at.strftime("%d.%m.%Y, %H:%M") if api_key.expires_at else "Faollashtirilmagan"
    
    from datetime import datetime
    if not api_key.activated_at:
        days_remaining = "15 kun (ishlatilganda boshlanadi)"
    else:
        delta = api_key.expires_at - datetime.utcnow()
        if delta.total_seconds() <= 0:
            days_remaining = "Muddati tugagan"
        else:
            days_remaining = f"{max(1, delta.days)} kun qoldi"
    
    return {
        "status": "ok",
        "key_name": getattr(api_key, "name", "API Key"),
        "created_at": created_at_str,
        "activated_at": activated_str,
        "expires_at": expires_str,
        "days_remaining": days_remaining,
        "balance_uzs": 500000,
        "votes_remaining": "Cheksiz (15 kun)",
        "total_votes_bought": "Cheksiz",
        "total_paid_uzs": total_paid_uzs,
        "is_active": api_key.is_active,
        "status_text": "Faol" if api_key.is_active else "Ega tomonidan o'chirilgan (@jahongir_1220)"
    }


@router.post("/buy-key-invoice")
async def create_buy_key_invoice(
    req: BuyKeyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    15 kunlik API kalit sotib olish uchun unikal tiyinli to'lov fakturasini yaratadi.
    """
    # Tarif narxini bazadan olamiz (yagona 15 kunlik obuna)
    tariffs = await crud.get_all_tariffs(db)
    if tariffs:
        price = tariffs[0].price
        tariff_name = tariffs[0].name
    else:
        price = 500000
        tariff_name = "15 kunlik API Kalit"
         
    settings_db = await crud.get_project_settings(db)
    if not settings_db.card_number:
        raise HTTPException(status_code=400, detail="To'lov qabul qilish kartasi sozlanmagan.")
        
    import random
    purchase = None
    
    for _ in range(50):
        try:
            random_cents = random.randint(1, 999)
            unique_price = price + random_cents
            existing = await crud.get_pending_purchase_by_unique_price(db, unique_price)
            if existing:
                continue
            purchase = await crud.create_pending_purchase(
                db=db,
                telegram_id=req.telegram_id,
                tariff_name=tariff_name,
                price_uzs=price,
                unique_price_uzs=unique_price,
                votes_count=15, # 15 kunni bildiradi
                source="CLIENT_BOT"
            )
            if req.target_key:
                purchase.generated_key = req.target_key
                await db.commit()
            break
        except Exception:
            await db.rollback()
            continue
            
    if not purchase:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="To'lov summasini band qilishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring."
        )
    
    return {
        "status": "success",
        "purchase_id": purchase.id,
        "tariff_name": tariff_name,
        "votes_count": 15,
        "base_price": price,
        "unique_price": purchase.unique_price_uzs,
        "card_number": settings_db.card_number
    }


@router.get("/check-purchase/{purchase_id}")
async def check_purchase_status(purchase_id: int, db: AsyncSession = Depends(get_db)):
    """
    Mijoz boti uchun to'lov holatini tekshiradi.
    To'lov tasdiqlangach yangi yaratilgan API kalitni qaytaradi.
    """
    purchase = await db.get(crud.APIKeyPurchase, purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail="Xarid topilmadi.")
        
    return {
        "status": purchase.status,
        "api_key": purchase.generated_key if purchase.status == "COMPLETED" else None,
        "votes_count": purchase.votes_count,
        "tariff_name": purchase.tariff_name
    }


@router.post("/cancel-key-invoice/{purchase_id}")
async def cancel_key_invoice(purchase_id: int, db: AsyncSession = Depends(get_db)):
    """
    Kutilayotgan to'lov fakturasini bekor qiladi.
    """
    purchase = await db.get(crud.APIKeyPurchase, purchase_id)
    if purchase and purchase.status == "PENDING":
        purchase.status = "CANCELLED"
    return {"status": "success"}


@router.get("/test-db-query")
async def test_db_query(db: AsyncSession = Depends(get_db)):
    import aiohttp
    import re
    from config import settings
    
    url = "https://openbudget.uz/api/v2/vote/mvc/captcha/0a70f4e1-0ca3-4407-8ac3-939cfa4a4653"
    proxy = settings.PROXY_URL or None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html",
    }
    
    out = {"url": url}
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, proxy=proxy) as resp:
                text = await resp.text()
                out["status"] = resp.status
                
                title_match = re.search(r'<title>(.*?)</title>', text, re.I)
                out["title"] = title_match.group(1).strip() if title_match else "No Title"
                
                # Check for Cloudflare
                out["is_cloudflare"] = "cloudflare" in text.lower() or "ddos" in text.lower()
                out["body_preview"] = text[:300]
    except Exception as e:
        out["error"] = str(e)
    return out
