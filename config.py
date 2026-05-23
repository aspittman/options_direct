import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]

DOLLARS_PER_TRADE = 100
MAX_POSITIONS = 2

MA_SHORT = 50
MA_LONG = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

MIN_DTE = 30
MAX_DTE = 60

OPTION_TYPE = "call"  # start with calls only
CONTRACT_QTY = 1

SCAN_INTERVAL_SECONDS = 300