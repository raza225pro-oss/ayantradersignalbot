import os
import asyncio
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]

# Pakistan Time (UTC+5)
PKT = timezone(timedelta(hours=5))

# Binance Futures (public data, no API key needed for scanning)
exchange = ccxt.binanceusdm({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# Scan list (top liquid coins)
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT']

# --- Helper Functions ---
def calculate_indicators(df):
    """Calculate RSI and EMA"""
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # EMA
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # Volume average
    df['vol_avg'] = df['volume'].rolling(window=20).mean()
    return df

def get_signal(symbol):
    """Scan a single symbol for signals"""
    try:
        # Fetch 15m candles (good for short-term signals)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = calculate_indicators(df)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        current_price = last['close']
        rsi = last['rsi']
        ema_fast = last['ema_fast']
        ema_slow = last['ema_slow']
        prev_ema_fast = prev['ema_fast']
        prev_ema_slow = prev['ema_slow']
        volume = last['volume']
        vol_avg = last['vol_avg']
        
        signal = None
        direction = None
        reason = []
        
        # Bullish Signal: EMA Fast crosses above EMA Slow + RSI not overbought
        if prev_ema_fast <= prev_ema_slow and ema_fast > ema_slow and rsi < 70:
            direction = "UPAR (LONG) 📈"
            reason.append(f"EMA Crossover (9 > 21)")
            reason.append(f"RSI: {rsi:.1f} (not overbought)")
            signal = True
        
        # Bearish Signal: EMA Fast crosses below EMA Slow + RSI not oversold
        elif prev_ema_fast >= prev_ema_slow and ema_fast < ema_slow and rsi > 30:
            direction = "NEECHAY (SHORT) 📉"
            reason.append(f"EMA Crossover (9 < 21)")
            reason.append(f"RSI: {rsi:.1f} (not oversold)")
            signal = True
        
        # Volume confirmation (optional)
        if signal and volume > vol_avg * 1.2:
            reason.append(f"Volume: {volume/vol_avg:.1f}x average")
        
        if signal:
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {
                'symbol': symbol,
                'direction': direction,
                'price': current_price,
                'time_pkt': now_pkt,
                'reason': "\n".join(reason)
            }
    except Exception as e:
        print(f"Error scanning {symbol}: {e}")
    return None

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Assalam-o-Alaikum! Main aapka Market Signal Bot hoon.\n\n"
        "Main Binance Futures market ko scan karta hoon aur signals deta hoon.\n"
        "Command: /scan (market scan karne ke liye)\n\n"
        "⚠️ Yeh financial advice nahi hai. Sirf technical analysis hai. Apna risk management khud karein."
    )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Market scan ho raha hai... (15m timeframe)")
    
    found_signals = []
    for sym in SYMBOLS:
        result = get_signal(sym)
        if result:
            found_signals.append(result)
        await asyncio.sleep(0.5)  # Rate limit
    
    if not found_signals:
        await update.message.reply_text(
            f"📊 Scan complete.\n"
            f"Abhi koi strong signal nahi mila.\n"
            f"Time (PKT): {datetime.now(PKT).strftime('%I:%M %p')}\n\n"
            f"Market monitor kar raha hoon. Thori der baad phir try karein."
        )
    else:
        for sig in found_signals:
            msg = (
                f"🚨 SIGNAL: {sig['symbol']}\n"
                f"Direction: {sig['direction']}\n"
                f"Price: ${sig['price']:.4f}\n"
                f"Time (PKT): {sig['time_pkt']}\n"
                f"Reason:\n{sig['reason']}\n\n"
                f"⚠️ Apna stop-loss lagayein. Yeh guarantee nahi hai."
            )
            await update.message.reply_text(msg)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sorry, aap is bot ko use nahi kar sakte.")

# --- Main ---
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Only allowed users can interact
    app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan_command))
    
    print(f"Bot started. Allowed IDs: {ALLOWED_IDS}")
    app.run_polling()

if __name__ == "__main__":
    main()