from aiogram.fsm.state import State, StatesGroup

class VoteStates(StatesGroup):
    WAITING_FOR_PHONE = State()    # Telefon raqamini kiritish jarayoni
    WAITING_FOR_CAPTCHA = State()  # Captcha yechish jarayoni
    WAITING_FOR_SMS = State()      # SMS kodini kiritish jarayoni
    WAITING_FOR_FINAL_CAPTCHA = State() # SMS dan keyingi yakuniy captcha yechish jarayoni
    WAITING_FOR_REGISTRATION_CHECK = State() # Ro'yxatdan o'tganligini tekshirish jarayoni


class WithdrawStates(StatesGroup):
    WAITING_FOR_CARD = State()   # Pul yechish uchun karta raqam kiritish

class AdminStates(StatesGroup):
    WAITING_FOR_PROJECT_ID = State()      # Loyiha ID sini o'zgartirish
    WAITING_FOR_PROJECT_CONFIRM = State() # Loyihani tasdiqlash holati
    WAITING_FOR_PROJECT_URL = State()     # Loyiha havolasini o'zgartirish
    WAITING_FOR_REFERRAL_PRICE = State()  # Har bir taklif/ovoz uchun mukofot miqdorini o'zgartirish
    WAITING_FOR_VOTER_REWARD = State()    # Ovoz bergan odamning o'ziga beriladigan mukofotni o'zgartirish
    WAITING_FOR_MIN_WITHDRAWAL = State()  # Minimal pul yechish chegarasini o'zgartirish
    WAITING_FOR_AD_TEXT = State()         # Reklama matnini qabul qilish holati
    WAITING_FOR_CHANNEL_USERNAME = State() # Maxfiy kanal username yoki linkini kiritish
    WAITING_FOR_ADMIN_CARD = State()      # To'lovlar uchun karta raqami sozlash
    WAITING_FOR_ADMIN_CHANNEL = State()   # To'lov kanali Telegram ID sini sozlash


