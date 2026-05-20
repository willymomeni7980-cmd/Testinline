import os

# Bot Token Ø§Ø² Ù…Ø­ÛŒØ· Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8757391333:AAEOrwa2vSdR7p2sWAxUV24onmQ-4e3_RLk")

# Ø¢ÛŒØ¯ÛŒ Ø§Ø¯Ù…ÛŒÙ† (Ø¹Ø¯Ø¯ÛŒ)
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "2083913926").split(",") if x.strip()]

# Ø´Ù…Ø§Ø±Ù‡ Ú©Ø§Ø±Øª Ø¨Ø±Ø§ÛŒ Ù¾Ø±Ø¯Ø§Ø®Øª
CARD_NUMBER = os.environ.get("CARD_NUMBER", "6037-9981-7623-7674")
CARD_HOLDER = os.environ.get("CARD_HOLDER", "Ù…ÙˆÙ…Ù†ÛŒ")

# Ø²Ù…Ø§Ù† Ø§Ù†Ù‚Ø¶Ø§ÛŒ Ù¾Ø±Ø¯Ø§Ø®Øª (Ø¯Ù‚ÛŒÙ‚Ù‡)
PAYMENT_TIMEOUT_MINUTES = 20

# Ù¾Ù„Ù†â€ŒÙ‡Ø§ (Ù‚ÛŒÙ…Øªâ€ŒÙ‡Ø§ Ø±Ø§ Ø§Ø² Ù…Ø­ÛŒØ· Ø¨Ø®ÙˆØ§Ù† ÛŒØ§ Ù¾ÛŒØ´â€ŒÙØ±Ø¶ Ø¨Ø§Ø´Ù†Ø¯)
PLANS = {
    "1gb": {
        "name": "Ø§Ø´ØªØ±Ø§Ú© Û± Ú¯ÛŒÚ¯",
        "size": "Û± Ú¯ÛŒÚ¯Ø§Ø¨Ø§ÛŒØª",
        "duration": "Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯",
        "price": int(os.environ.get("PRICE_1GB", "350000")),
    },
    "2gb": {
        "name": "Ø§Ø´ØªØ±Ø§Ú© Û² Ú¯ÛŒÚ¯",
        "size": "Û² Ú¯ÛŒÚ¯Ø§Ø¨Ø§ÛŒØª",
        "duration": "Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯",
        "price": int(os.environ.get("PRICE_2GB", "650000")),
    },
}

TEST_PLANS = {
    "50mb": {
        "name": "ÛµÛ° Ù…Ú¯Ø§Ø¨Ø§ÛŒØª ØªØ³Øª",
        "size": "ÛµÛ° Ù…Ú¯Ø§Ø¨Ø§ÛŒØª",
        "price": int(os.environ.get("PRICE_TEST_50MB", "45000")),
    },
    "100mb": {
        "name": "Û±Û°Û° Ù…Ú¯Ø§Ø¨Ø§ÛŒØª ØªØ³Øª",
        "size": "Û±Û°Û° Ù…Ú¯Ø§Ø¨Ø§ÛŒØª",
        "price": int(os.environ.get("PRICE_TEST_100MB", "85000")),
    },
}

# ØªØ¹Ø¯Ø§Ø¯ Ø¯Ø¹ÙˆØª Ø¨Ø±Ø§ÛŒ Ø¯Ø±ÛŒØ§ÙØª ØªØ³Øª Ø±Ø§ÛŒÚ¯Ø§Ù†
REFERRAL_THRESHOLD = 5
