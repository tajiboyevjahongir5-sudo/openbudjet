import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from database.session import get_db
from database.models import APIKey
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


# --- API Yo'llari (Endpoints) ---

@router.get("/reset-vote/{phone}")
@router.delete("/reset-vote/{phone}")
async def reset_vote_endpoint(
    phone: str,
    db: AsyncSession = Depends(get_db)
):
    """Test uchun telefon raqamining barcha ovozlar tarixini bazadan tozalaydi"""
    from sqlalchemy import text
    clean_p = "".join(filter(str.isdigit, phone))
    last_digits = clean_p[-9:] if len(clean_p) >= 9 else clean_p
    res = await db.execute(text(f"DELETE FROM votes_history WHERE phone_number LIKE '%{last_digits}%'"))
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
async def get_initiative_info(
    project_id: str
):
    """
    Kiritilgan ID (raqamli ID yoki UUID) bo'yicha loyiha ma'lumotlarini qidirib topadi.
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


@router.post("/send-otp")
async def send_otp(
    req: OTPRequest,
    api_key: APIKey = Depends(get_api_key)
):
    """
    Kiritilgan telefon raqam va captcha natijasi asosida SMS tasdiqlash kodini yuboradi.
    Narxi: Bepul
    """
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
        "otp_key": session_data.get("otp_key") if session_data else None
    }


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
    api_key: APIKey = Depends(get_api_key)
):
    """
    Open Budget'da ro'yxatdan o'tmagan yangi fuqaro uchun SMS OTP yuborish.
    Narxi: Bepul
    """
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
async def verify_otp(
    req: VerifyRequest,
    api_key: APIKey = Depends(get_api_key)
):
    """
    Kiritilgan SMS kodni tekshiradi va muvaffaqiyatli bo'lsa login tokenini qaytaradi.
    Narxi: Bepul
    """
    session_data = {
        "otp_key": req.otp_key,
        "phone": req.phone_number
    }
    
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
    Narxi: Muvaffaqiyatli ovoz berilsagina API kalit balansidan 1 500 so'm yozib olinadi.
    """
    # 1. Balansdan mablag'ni atomik tarzda band qilamiz (Race Condition va Double-Spend oldi olinadi)
    deducted = await crud.deduct_api_key_balance(db, api_key.id, 1500)
    if not deducted:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="API kalit balansi yetarli emas (kamida 1 500 UZS bo'lishi shart)."
        )
        
    # 2. Ovoz berish so'rovini yuboramiz (istalgan kutilmagan xatoda ham mablag' avtomatik qaytariladi)
    try:
        success, result_msg = await OpenBudgetService.cast_vote(
            project_id=req.project_id,
            access_token=req.access_token,
            captcha_key=req.captcha_key,
            captcha_result=req.captcha_result
        )
    except asyncio.CancelledError:
        # Foydalanuvchi so'rovni uzib qo'ygan holatda yangi mustaqil DB sessiyasi orqali kafolatli qaytariladi
        from database.session import async_session
        async with async_session() as new_db:
            await asyncio.shield(crud.update_api_key_balance(new_db, api_key.id, 1500))
        logger.warning(f"cast_vote so'rovi bekor qilindi (CancelledError). Mablag' yangi sessiyada qaytarildi.")
        raise
    except Exception as e:
        await crud.update_api_key_balance(db, api_key.id, 1500)
        logger.error(f"cast_vote kutilmagan xatolik: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tashqi OpenBudget serveri bilan ulanishda xatolik yuz berdi. Mablag' balansingizga qaytarildi."
        )
    
    if not success:
        # Ovoz berish muvaffaqiyatsiz tugasa, band qilingan mablag' to'liq qaytariladi (Refund)
        await crud.update_api_key_balance(db, api_key.id, 1500)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result_msg
        )
        
    return {
        "status": "success",
        "message": "Ovoz muvaffaqiyatli qabul qilindi!",
        "detail": result_msg
    }


# --- API Kalit Sotib Olish (Tijorat va Integratsiya) ---

class BuyKeyRequest(BaseModel):
    telegram_id: int = Field(..., description="Xaridorning Telegram ID raqami")
    votes: int = Field(..., description="Tanlangan tarifdagi ovozlar soni")


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


@router.post("/buy-key-invoice")
async def create_buy_key_invoice(
    req: BuyKeyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    API kalit sotib olish uchun unikal tiyinli to'lov fakturasini yaratadi.
    To'lov bank kartasiga tushishi bilan asosiy bot avtomatik aniqlab kalitni yaratadi.
    """
    tariff = await crud.get_tariff_by_votes(db, req.votes)
    if not tariff:
        raise HTTPException(status_code=404, detail="Bunday tarif topilmadi.")
        
    settings_db = await crud.get_project_settings(db)
    if not settings_db.card_number:
        raise HTTPException(status_code=400, detail="To'lov qabul qilish kartasi sozlanmagan.")
        
    import random
    price = tariff.price
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
                tariff_name=tariff.name,
                price_uzs=price,
                unique_price_uzs=unique_price,
                votes_count=req.votes,
                source="CLIENT_BOT"
            )
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
        "tariff_name": tariff.name,
        "votes_count": req.votes,
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
        await db.commit()
    return {"status": "success"}
