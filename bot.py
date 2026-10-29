import os
import asyncio
import threading
from flask import Flask
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Bot Setup ---
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]
PKT = timezone(timedelta(hours=5))

exchange = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 
    'LINK/USDT', 'DOT/USDT', 'MATIC/USDT', 'LTC/USDT', 'BCH/USDT', 'UNI/USDT', 'ATOM/USDT', 
    'ETC/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'SUI/USDT', 'NEAR/USDT', 
    'INJ/USDT', 'TIA/USDT', 'SEI/USDT', 'RUNE/USDT', 'GALA/USDT', 'SAND/USDT', 'MANA/USDT',
    'PEPE/USDT', 'SHIB/USDT', 'WIF/USDT', 'FLOKI/USDT', 'BONK/USDT', 'JUP/USDT', 'PYTH/USDT',
    'ORDI/USDT', '1000SATS/USDT', 'FET/USDT', 'AGIX/USDT', 'RNDR/USDT', 'IMX/USDT', 'STX/USDT'
]

def get_signal(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        if not ohlcv: return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Indicators calculate karein
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
        
        df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        last = df.iloc[-1]
        
        direction, confidence, reason = None, 0, []

        # --- RELAXED LOGIC (Koi bhi ek indicator match ho toh signal) ---
        
        # 1. UPAR ka Signal
        if last['ema_fast'] > last['ema_slow']: # EMA Up
            confidence = 60
            reason.append("EMA Up")
        elif last['macd'] > last['macd_signal']: # MACD Up
            confidence = 60
            reason.append("MACD Up")
        elif last['rsi'] > 55: # RSI Up
            confidence = 60
            reason.append("RSI Up")

        # 2. NEECHAY ka Signal
        if last['ema_fast'] < last['ema_slow']: # EMA Down
            if confidence == 0: confidence = 60
            else: confidence = 70 # Agar 2 indicator match karein toh zyada confidence
            reason.append("EMA Down")
        elif last['macd'] < last['macd_signal']: # MACD Down
            if confidence == 0: confidence = 60
            else: confidence = 70
            reason.append("MACD Down")
        elif last['rsi'] < 45: # RSI Down
            if confidence == 0: confidence = 60
            else: confidence = 70
            reason.append("RSI Down")

        # Direction set karein based on the strongest indicator
        if "Down" in " ".join(reason):
            direction = "NEECHAY (SHORT) 📉"
            if "Up" in " ".join(reason):
                direction = "MIXED (Wait) ⚖️" # Agar dono taraf ke signals hon
        elif "Up" in " ".join(reason):
            direction = "UPAR (LONG) 📈"

        if direction and confidence >= 60:
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol, 'direction': direction, 'confidence': confidence,
                'price': last['close'], 'time_pkt': now_pkt, 'reason': " + ".join(reason)
            }
            
    except Exception as e:
        pass
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalam-o-Alaikum! Main aapka Market Scanner Bot hoon.\n\nPoore market ko scan karne ke liye /scan likhein.")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Poore market ko scan kiya ja raha hai...\nThora intezar karein.")
    
    found_signals = []
    for sym in SYMBOLS:
        res = get_signal(sym)
        if res:
            found_signals.append(res)
        await asyncio.sleep(0.1) # Fast scan
    
    await msg.delete()
    
    if not found_signals:
        await update.message.reply_text("📊 Market bilkul flat hai. Abhi kuch bhi nahi mila.")
    else:
        # Top 5 signals
        found_signals.sort(key=lambda x: x['confidence'], reverse=True)
        for sig in found_signals[:5]:
            txt = (
                f"🚨 **{sig['symbol']}**\n"
                f"Direction: {sig['direction']}\n"
                f"Confidence: {sig['confidence']}%\n"
                f"Price: ${sig['price']:.4f}\n"
                f"⏰ Time (PKT): {sig['time_pkt']}\n"
                f"Reason: {sig['reason']}"
            )
            await update.message.reply_text(txt, parse_mode='Markdown')
            await asyncio.sleep(0.5)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sorry, aap is bot ko use nahi kar sakte.")

def run_bot():
    print("Bot thread started...")
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    
    telegram_app.run_polling()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    run_bot()
