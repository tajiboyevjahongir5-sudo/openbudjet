from aiogram.fsm.state import State, StatesGroup

class VoteStates(StatesGroup):
    WAITING_FOR_PHONE = State()    # Telefon raqamini kiritish jarayoni
    WAITING_FOR_CAPTCHA = State()  # Captcha yechish jarayoni
    WAITING_FOR_SMS = State()      # SMS kodini kiritish jarayoni

class WithdrawStates(StatesGroup):
    WAITING_FOR_CARD = State()   # Pul yechish uchun karta raqam kiritish

class AdminStates(StatesGroup):
    WAITING_FOR_PROJECT_ID = State()      # Loyiha ID sini o'zgartirish
    WAITING_FOR_PROJECT_URL = State()     # Loyiha havolasini o'zgartirish
    WAITING_FOR_REFERRAL_PRICE = State()  # Har bir taklif/ovoz uchun mukofot miqdorini o'zgartirish
    WAITING_FOR_MIN_WITHDRAWAL = State()  # Minimal pul yechish chegarasini o'zgartirish
    WAITING_FOR_AD_TEXT = State()         # Reklama matnini qabul qilish holati
