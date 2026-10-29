import os
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]
PKT = timezone(timedelta(hours=5))

exchange = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

# Zyada coins add kar diye hain taake market scan ho
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT']

def calculate_indicators(df):
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # EMA
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # MACD
    df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    
    return df

def get_signal(symbol):
    try:
        # 5m timeframe taake jaldi aur zyada signals milein
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = calculate_indicators(df)
        last, prev = df.iloc[-1], df.iloc[-2]
        
        direction = None
        signal_strength = "Weak (Mild)"
        reason = []
        
        # Up Trend Condition (Strong)
        if prev['ema_fast'] <= prev['ema_slow'] and last['ema_fast'] > last['ema_slow'] and last['rsi'] < 70:
            direction = "UPAR 📈"
            signal_strength = "Strong"
            reason.append("EMA Crossover (9 > 21)")
            reason.append(f"RSI: {last['rsi']:.1f}")
        
        # Down Trend Condition (Strong)
        elif prev['ema_fast'] >= prev['ema_slow'] and last['ema_fast'] < last['ema_slow'] and last['rsi'] > 30:
            direction = "NEECHAY 📉"
            signal_strength = "Strong"
            reason.append("EMA Crossover (9 < 21)")
            reason.append(f"RSI: {last['rsi']:.1f}")
            
        # Agar Strong nahi mila, toh MACD aur RSI se Weak/Mild signal dekhein
        elif last['macd'] > last['macd_signal'] and last['rsi'] < 60 and last['rsi'] > 40:
            if last['close'] > last['ema_fast']: # Price moving average ke upar hai
                direction = "UPAR (Mild) 📈"
                reason.append("MACD Bullish & Price > EMA9")
                reason.append(f"RSI: {last['rsi']:.1f}")
        elif last['macd'] < last['macd_signal'] and last['rsi'] > 40 and last['rsi'] < 60:
            if last['close'] < last['ema_fast']: # Price moving average ke neeche hai
                direction = "NEECHAY (Mild) 📉"
                reason.append("MACD Bearish & Price < EMA9")
                reason.append(f"RSI: {last['rsi']:.1f}")

        if direction:
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol, 
                'direction': direction, 
                'strength': signal_strength,
                'price': last['close'], 
                'time_pkt': now_pkt, 
                'reason': " | ".join(reason)
            }
    except Exception as e:
        print(f"Error scanning {symbol}: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalam-o-Alaikum! Main aapka Market Signal Bot hoon.\n\nCommand: /scan")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Market scan ho raha hai... (5m timeframe)")
    found_signals = []
    
    for s in SYMBOLS:
        res = get_signal(s)
        if res: 
            found_signals.append(res)
    
    if not found_signals:
        await update.message.reply_text("📊 Market bilkul flat hai. Abhi koi clear movement nahi hai. 5 minute baad try karein.")
    else:
        for sig in found_signals:
            msg = (
                f"🚨 **{sig['symbol']}**\n"
                f"Direction: {sig['direction']}\n"
                f"Strength: {sig['strength']}\n"
                f"Price: ${sig['price']:.4f}\n"
                f"⏰ Time (PKT): {sig['time_pkt']}\n"
                f"Reason: {sig['reason']}"
            )
            await update.message.reply_text(msg, parse_mode='Markdown')

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sorry, aap is bot ko use nahi kar sakte.")

if __name__ == "__main__":
    print("Starting upgraded bot...")
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    
    telegram_app.run_polling()
