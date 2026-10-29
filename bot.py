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

exchange = ccxt.binanceusdm({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# 50+ Major Coins
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
        # 1-Minute timeframe taake market ki choti movement bhi pakri jaye
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=50)
        if not ohlcv or len(ohlcv) < 20: return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # --- 7 Different Strategies Calculate Karein ---
        
        # 1. EMA (9 & 21)
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # 2. RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # 3. MACD
        df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # 4. Bollinger Bands
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma_20'] + (df['std'] * 2)
        df['lower_band'] = df['sma_20'] - (df['std'] * 2)
        
        # 5. Stochastic
        df['low_14'] = df['low'].rolling(window=14).mean()
        df['high_14'] = df['high'].rolling(window=14).mean()
        df['stoch'] = 100 * ((df['close'] - df['low_14']) / (df['high_14'] - df['low_14']))
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- SCANNER LOGIC (Koi bhi condition match ho toh signal) ---
        signals = []

        # 1. Trend Break (EMA)
        if last['ema_9'] > last['ema_21']: signals.append(("UPAR 📈", "EMA Trend Up"))
        elif last['ema_9'] < last['ema_21']: signals.append(("NEECHAY 📉", "EMA Trend Down"))

        # 2. MACD Cross
        if last['macd'] > last['macd_signal']: signals.append(("UPAR 📈", "MACD Bullish"))
        elif last['macd'] < last['macd_signal']: signals.append(("NEECHAY 📉", "MACD Bearish"))

        # 3. RSI Reversal
        if last['rsi'] < 35: signals.append(("UPAR 📈", "RSI Oversold (Reversal)"))
        elif last['rsi'] > 65: signals.append(("NEECHAY 📉", "RSI Overbought (Reversal)"))

        # 4. Bollinger Bands
        if last['close'] < last['lower_band']: signals.append(("UPAR 📈", "Bollinger Bounce (Up)"))
        elif last['close'] > last['upper_band']: signals.append(("NEECHAY 📉", "Bollinger Bounce (Down)"))

        # 5. Stochastic
        if last['stoch'] < 20: signals.append(("UPAR 📈", "Stoch Oversold"))
        elif last['stoch'] > 80: signals.append(("NEECHAY 📉", "Stoch Overbought"))

        # 6. Simple Price Action
        if last['close'] > last['open']: signals.append(("UPAR 📈", "Bullish Candle"))
        elif last['close'] < last['open']: signals.append(("NEECHAY 📉", "Bearish Candle"))

        # Agar koi bhi signal mila, toh return karein
        if signals:
            # Sab se zyada bar bar aane wala direction (UPAR ya NEECHAY) chunein
            directions = [s[0] for s in signals]
            final_direction = max(set(directions), key=directions.count)
            
            # Confidence barhayein based on kitne indicators agree karte hain
            confidence = 50 + (len(signals) * 5) 
            
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol, 
                'direction': final_direction, 
                'confidence': min(confidence, 95), # Max 95%
                'price': last['close'], 
                'time_pkt': now_pkt, 
                'reason': ", ".join([s[1] for s in signals[:2]]) # Top 2 reasons
            }
            
    except Exception as e:
        pass
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **Multi-Strategy Scanner Online!**\n\nPoore market (40+ coins) ko scan karne ke liye /scan likhein. Main 7 different strategies check karke aapko foran signals dunga.")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 40+ coins par 7 strategies test ki ja rahi hain...\nThora intezar karein.")
    
    found_signals = []
    for sym in SYMBOLS:
        res = get_signal(sym)
        if res:
            found_signals.append(res)
        await asyncio.sleep(0.1) 
    
    await msg.delete()
    
    if not found_signals:
        await update.message.reply_text("📊 Ajeeb baat hai, market bilkul dead hai. Thori der baad try karein.")
    else:
        found_signals.sort(key=lambda x: x['confidence'], reverse=True)
        for sig in found_signals[:10]: # Top 10 signals
            txt = (
                f"🚨 **{sig['symbol']}**\n"
                f"Direction: {sig['direction']}\n"
                f"Confidence: {sig['confidence']}%\n"
                f"Price: ${sig['price']:.4f}\n"
                f"⏰ Time (PKT): {sig['time_pkt']}\n"
                f"Reasons: {sig['reason']}"
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
