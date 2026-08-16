from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from database.session import get_db
from database.models import APIKey
from database import crud
from utils.api_auth import get_api_key
from services.openbudget import OpenBudgetService

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

class VoteRequest(BaseModel):
    project_id: str = Field(..., description="Loyiha ID raqami yoki UUID kaliti")
    access_token: str = Field(..., description="Verify-OTP bosqichida olingan login tokeni")
    captcha_key: str = Field(..., description="2-captcha kaliti")
    captcha_result: int = Field(..., description="2-captcha yechimi (rasmdagi raqamlar)")


# --- API Yo'llari (Endpoints) ---

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
    project_id: str,
    api_key: APIKey = Depends(get_api_key)
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
        # Foydalanuvchi so'rovni uzib qo'ygan holatda ham mablag' qaytariladi
        await crud.update_api_key_balance(db, api_key.id, 1500)
        logger.warning(f"cast_vote so'rovi bekor qilindi (CancelledError). Mablag' qaytarildi.")
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
