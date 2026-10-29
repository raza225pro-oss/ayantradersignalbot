import os
import ccxt
import pandas as pd
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]
PKT = timezone(timedelta(hours=5))

exchange = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

# Poore market ki jagah top 50+ major coins (taake API block na ho)
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
        
        # Indicators
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
        
        last, prev = df.iloc[-1], df.iloc[-2]
        direction, confidence, reason = None, 0, []

        # 1. UPAR ka Signal (60%+ confidence)
        if last['ema_fast'] > last['ema_slow'] and last['close'] > last['ema_fast']:
            confidence = 60
            reason.append("EMA Uptrend")
            if last['rsi'] > 50: 
                confidence += 20
                reason.append("RSI > 50")
            if last['rsi'] > 70:
                confidence -= 15 # Overbought risk
            direction = "UPAR (LONG) 📈"

        # 2. NEECHAY ka Signal (60%+ confidence)
        elif last['ema_fast'] < last['ema_slow'] and last['close'] < last['ema_fast']:
            confidence = 60
            reason.append("EMA Downtrend")
            if last['rsi'] < 50: 
                confidence += 20
                reason.append("RSI < 50")
            if last['rsi'] < 30:
                confidence -= 15 # Oversold risk
            direction = "NEECHAY (SHORT) 📉"

        # Agar confidence 60% ya us se zyada hai, toh signal return karein
        if direction and confidence >= 60:
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol, 'direction': direction, 'confidence': confidence,
                'price': last['close'], 'time_pkt': now_pkt, 'reason': " | ".join(reason)
            }
    except Exception as e:
        pass # Error aane par agla coin check karein
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalam-o-Alaikum! Main aapka Market Scanner Bot hoon.\n\nPoore market ko scan karne ke liye /scan likhein.")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Poore market (40+ coins) ko scan kiya ja raha hai...\nThora intezar karein (is mein 1 minute lag sakta hai).")
    
    found_signals = []
    
    # Har coin ko ek ek kar ke scan karein (Rate limit se bachne ke liye)
    for sym in SYMBOLS:
        res = get_signal(sym)
        if res:
            found_signals.append(res)
        await asyncio.sleep(0.2) # 0.2 seconds ka break taake Binance block na kare
    
    # Delete "Scanning..." message
    await msg.delete()
    
    if not found_signals:
        await update.message.reply_text(f"📊 Poore market mein abhi 60%+ ka koi signal nahi hai.\nTime (PKT): {datetime.now(PKT).strftime('%I:%M %p')}")
    else:
        # Signals ko confidence ke hisaab se sort karein (Top wale pehle)
        found_signals.sort(key=lambda x: x['confidence'], reverse=True)
        
        # Top 5 best signals bhejein taake chat spam na ho
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

if __name__ == "__main__":
    print("Starting Advanced Scanner Bot...")
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    
    telegram_app.run_polling()
