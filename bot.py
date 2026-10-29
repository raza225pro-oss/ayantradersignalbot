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
        # Timeframe 1m kar diya hai taake jaldi signals milein
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=100)
        if not ohlcv or len(ohlcv) < 30: return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. EMA Trend
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # 2. RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # 3. Stochastic
        df['low_14'] = df['low'].rolling(window=14).mean()
        df['high_14'] = df['high'].rolling(window=14).mean()
        df['stoch'] = 100 * ((df['close'] - df['low_14']) / (df['high_14'] - df['low_14']))
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        direction = None
        confidence = 0
        reason = []

        # --- GUESS & STRATEGY SYSTEM ---

        # Strategy 1: Trend Following (EMA)
        if last['ema_9'] > last['ema_21'] and last['close'] > last['ema_9']:
            direction = "UPAR (LONG) 📈"
            confidence += 65
            reason.append("EMA Trend Up")
        elif last['ema_9'] < last['ema_21'] and last['close'] < last['ema_9']:
            direction = "NEECHAY (SHORT) 📉"
            confidence += 65
            reason.append("EMA Trend Down")

        # Strategy 2: Momentum (RSI & Stoch)
        if last['rsi'] < 35: # Oversold (Upar jane ke chances)
            if direction == "NEECHAY (SHORT) 📉": direction = "UPAR (LONG) 📈" # Reversal
            confidence += 10
            reason.append("RSI Oversold")
        elif last['rsi'] > 65: # Overbought (Neeche jane ke chances)
            if direction == "UPAR (LONG) 📈": direction = "NEECHAY (SHORT) 📉"
            confidence += 10
            reason.append("RSI Overbought")

        # Strategy 3: Breakout (Agar koi trend nahi mila)
        if direction is None:
            # Agar price ne pichle 10 candles ki high/low break ki hai
            if last['close'] > df['high'].tail(10).max():
                direction = "UPAR (LONG) 📈"
                confidence = 60
                reason.append("Breakout High")
            elif last['close'] < df['low'].tail(10).min():
                direction = "NEECHAY (SHORT) 📉"
                confidence = 60
                reason.append("Breakout Low")
            else:
                # Agar market bilkul flat hai, toh Guess System (Random based on momentum)
                if last['close'] > last['open']:
                    direction = "UPAR (GUESS) 📈"
                    confidence = 50
                    reason.append("Momentum Guess")
                else:
                    direction = "NEECHAY (GUESS) 📉"
                    confidence = 50
                    reason.append("Momentum Guess")

        if direction:
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol, 'direction': direction, 'confidence': confidence,
                'price': last['close'], 'time_pkt': now_pkt, 'reason': " + ".join(reason)
            }
            
    except Exception as e:
        print(f"Error: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalam-o-Alaikum! Main aapka Market Scanner Bot hoon.\n\nPoore market ko scan karne ke liye /scan likhein.")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Market scan ho raha hai...")
    
    found_signals = []
    for sym in SYMBOLS:
        res = get_signal(sym)
        if res:
            found_signals.append(res)
        await asyncio.sleep(0.1) 
    
    await msg.delete()
    
    if not found_signals:
        await update.message.reply_text("📊 Market mein abhi koi clear trend nahi hai.")
    else:
        # Top 10 signals bhejein taake market ka poora haal pata chale
        found_signals.sort(key=lambda x: x['confidence'], reverse=True)
        for sig in found_signals[:10]:
            txt = (
                f"🚨 **{sig['symbol']}**\n"
                f"Direction: {sig['direction']}\n"
                f"Confidence: {sig['confidence']}%\n"
                f"Price: ${sig['price']:.4f}\n"
                f"⏰ Time (PKT): {sig['time_pkt']}\n"
                f"Strategy: {sig['reason']}"
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
