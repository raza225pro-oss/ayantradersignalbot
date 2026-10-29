import os
import asyncio
import threading
from flask import Flask, request
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

app = Flask(__name__)
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]
PKT = timezone(timedelta(hours=5))

exchange = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT']

# Telegram App setup
telegram_app = Application.builder().token(TOKEN).build()

def calculate_indicators(df):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    df['vol_avg'] = df['volume'].rolling(window=20).mean()
    return df

def get_signal(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = calculate_indicators(df)
        last, prev = df.iloc[-1], df.iloc[-2]
        
        signal, direction, reason = None, None, []
        
        if prev['ema_fast'] <= prev['ema_slow'] and last['ema_fast'] > last['ema_slow'] and last['rsi'] < 70:
            direction = "UPAR (LONG) 📈"
            reason.append("EMA Crossover (9 > 21)")
            reason.append(f"RSI: {last['rsi']:.1f}")
            signal = True
        elif prev['ema_fast'] >= prev['ema_slow'] and last['ema_fast'] < last['ema_slow'] and last['rsi'] > 30:
            direction = "NEECHAY (SHORT) 📉"
            reason.append("EMA Crossover (9 < 21)")
            reason.append(f"RSI: {last['rsi']:.1f}")
            signal = True
        
        if signal:
            if last['volume'] > last['vol_avg'] * 1.2:
                reason.append(f"Volume: {last['volume']/last['vol_avg']:.1f}x")
            now_pkt = datetime.now(PKT).strftime("%I:%M %p")
            return {'symbol': symbol, 'direction': direction, 'price': last['close'], 'time_pkt': now_pkt, 'reason': "\n".join(reason)}
    except Exception as e:
        print(f"Error: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalam-o-Alaikum! Main aapka Market Signal Bot hoon.\n\nCommand: /scan")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Market scan ho raha hai...")
    found = []
    for s in SYMBOLS:
        res = get_signal(s)
        if res: found.append(res)
    
    if not found:
        await update.message.reply_text(f"📊 Scan complete. Koi strong signal nahi.\nTime (PKT): {datetime.now(PKT).strftime('%I:%M %p')}")
    else:
        for sig in found:
            msg = f"🚨 SIGNAL: {sig['symbol']}\nDirection: {sig['direction']}\nPrice: ${sig['price']:.4f}\nTime (PKT): {sig['time_pkt']}\nReason:\n{sig['reason']}"
            await update.message.reply_text(msg)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sorry, aap is bot ko use nahi kar sakte.")

telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("scan", scan_command))

# --- Webhook Routes ---
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        # Use asyncio to process update in background
        asyncio.run_coroutine_threadsafe(telegram_app.process_update(update), telegram_app.loop)
        return "OK", 200
    return "Method Not Allowed", 405

def set_webhook():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if url:
        webhook_url = f"{url}/webhook/{TOKEN}"
        print(f"Setting webhook to: {webhook_url}")
        # Use requests to set webhook
        import requests
        response = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}")
        print(response.json())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Initialize bot
    telegram_app.initialize()
    telegram_app.start()
    
    # Set webhook (agar Render URL available ho)
    set_webhook()
    
    # Run Flask server
    app.run(host="0.0.0.0", port=port)
