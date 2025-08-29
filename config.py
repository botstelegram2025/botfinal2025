import os
from datetime import timedelta

class Config:
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/telegram_bot")
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
    
    # WhatsApp (Baileys Gateway)
    WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", "http://localhost:3001")
    WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
    WHATSAPP_SESSION_ID = os.getenv("WHATSAPP_SESSION_ID", "1")
    
    # (Retrocompatibilidade - se já usa BAYLEYS_* em produção)
    BAYLEYS_API_URL = os.getenv("BAYLEYS_API_URL", WHATSAPP_SERVICE_URL)
    BAYLEYS_API_KEY = os.getenv("BAYLEYS_API_KEY", WHATSAPP_API_TOKEN)
    BAYLEYS_INSTANCE_ID = os.getenv("BAYLEYS_INSTANCE_ID", WHATSAPP_SESSION_ID)
    
    # Mercado Pago
    MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "your_mp_access_token")
    MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY", "your_mp_public_key")
    MERCADO_PAGO_WEBHOOK_SECRET = os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "")
    
    # Subscription Settings
    TRIAL_PERIOD_DAYS = int(os.getenv("TRIAL_PERIOD_DAYS", "7"))
    MONTHLY_SUBSCRIPTION_PRICE = float(os.getenv("MONTHLY_SUBSCRIPTION_PRICE", "20.00"))
    
    # Reminder Settings
    REMINDER_DAYS = [-2, -1, 0, 1]  # Dias relativos à data de vencimento
    
    # Timezone
    TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # App Metadata
    APP_NAME = os.getenv("APP_NAME", "Client Management Bot")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
