import os
import asyncio
import threading
import logging
from flask import Flask
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# Logging setup taake console me har error saaf nazar aaye
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
raw_ids = os.getenv("ALLOWED_IDS", "")
ALLOWED_IDS = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
PKT = timezone(timedelta(hours=5))

# --- Smart Multi-Exchange Setup (Agar server IP par Binance block ho toh Bybit/KuCoin use karega) ---
def init_exchange():
    exchanges_to_try = [
        ('binanceusdm', {'enableRateLimit': True, 'options': {'defaultType': 'future'}}),
        ('binance', {'enableRateLimit': True}),
        ('bybit', {'enableRateLimit': True}),
        ('kucoin', {'enableRateLimit': True})
    ]
    for ex_name, config in exchanges_to_try:
        try:
            ex_class = getattr(ccxt, ex_name)
            ex = ex_class(config)
            ex.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=5)
            logger.info(f"✅ Successfully connected to exchange: {ex_name.upper()}")
            return ex
        except Exception as e:
            logger.warning(f"⚠️ {ex_name} failed ({e}), trying next exchange...")
    # Fallback default
    return ccxt.binance({'enableRateLimit': True})

exchange = init_exchange()

# Updated Active Coins List (Removed dead/delisted tickers like MATIC, AGIX, RNDR)
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT',
    'LINK/USDT', 'DOT/USDT', 'POL/USDT', 'LTC/USDT', 'BCH/USDT', 'UNI/USDT', 'ATOM/USDT',
    'ETC/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'SUI/USDT', 'NEAR/USDT',
    'INJ/USDT', 'TIA/USDT', 'SEI/USDT', 'RUNE/USDT', 'GALA/USDT', 'SAND/USDT', 'MANA/USDT',
    'PEPE/USDT', 'SHIB/USDT', 'WIF/USDT', 'FLOKI/USDT', 'BONK/USDT', 'JUP/USDT', 'PYTH/USDT',
    'ORDI/USDT', 'FET/USDT', 'RENDER/USDT', 'IMX/USDT', 'STX/USDT', 'TON/USDT', 'TRX/USDT',
    'AAVE/USDT', 'CRV/USDT', 'MKR/USDT', 'PENDLE/USDT', 'ENA/USDT', 'TAO/USDT', 'ONDO/USDT'
]

def calculate_indicators_and_signal(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=60)
        if not ohlcv or len(ohlcv) < 30:
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # 1. EMAs (Fast 5, 9, 21, 50)
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

        # 2. Correct Wilder's RSI (14)
        delta = df['close'].diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)

        # 3. MACD (12, 26, 9)
        df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']

        # 4. Bollinger Bands (20, 2)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma_20'] + (df['std'] * 2)
        df['lower_band'] = df['sma_20'] - (df['std'] * 2)

        # 5. Correct Stochastic Oscillator (14, 3)
        df['low_14'] = df['low'].rolling(window=14).min()
        df['high_14'] = df['high'].rolling(window=14).max()
        denom = (df['high_14'] - df['low_14']).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - df['low_14']) / denom)).fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        # 6. VWAP (Rolling 20-period intraday approximation)
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (typical_price * df['volume']).rolling(window=20).sum() / df['volume'].rolling(window=20).sum().replace(0, np.nan)

        # 7. Volume Spike & Rate of Change (ROC)
        df['vol_ma'] = df['volume'].rolling(window=20).mean()
        df['roc'] = df['close'].pct_change(periods=3) * 100

        last = df.iloc[-1]
        prev = df.iloc[-2]

        long_reasons = []
        short_reasons = []

        # --- 12 HIGH-SENSITIVITY TRIGGER STRATEGIES ---

        # Strategy 1: EMA 9/21 Fresh Crossover (High Weight)
        if prev['ema_9'] <= prev['ema_21'] and last['ema_9'] > last['ema_21']:
            long_reasons.append(("EMA 9/21 Bullish Cross", 15))
        elif prev['ema_9'] >= prev['ema_21'] and last['ema_9'] < last['ema_21']:
            short_reasons.append(("EMA 9/21 Bearish Cross", 15))

        # Strategy 2: Fast Scalp EMA 5/9 Alignment
        if last['ema_5'] > last['ema_9'] > last['ema_21']:
            long_reasons.append(("Strong Bullish EMA Stack", 8))
        elif last['ema_5'] < last['ema_9'] < last['ema_21']:
            short_reasons.append(("Strong Bearish EMA Stack", 8))

        # Strategy 3: MACD Histogram Flip / Cross
        if prev['macd_hist'] <= 0 and last['macd_hist'] > 0:
            long_reasons.append(("MACD Fresh Bullish Cross", 15))
        elif prev['macd_hist'] >= 0 and last['macd_hist'] < 0:
            short_reasons.append(("MACD Fresh Bearish Cross", 15))
        elif last['macd'] > last['macd_signal']:
            long_reasons.append(("MACD Bullish Momentum", 6))
        else:
            short_reasons.append(("MACD Bearish Momentum", 6))

        # Strategy 4: RSI Extremes & Momentum Zones
        if last['rsi'] < 32:
            long_reasons.append((f"RSI Oversold ({last['rsi']:.1f})", 14))
        elif last['rsi'] > 68:
            short_reasons.append((f"RSI Overbought ({last['rsi']:.1f})", 14))
        elif 52 < last['rsi'] <= 68:
            long_reasons.append(("RSI Bullish Zone", 5))
        elif 32 <= last['rsi'] < 48:
            short_reasons.append(("RSI Bearish Zone", 5))

        # Strategy 5: Bollinger Band Breakout / Mean Reversion
        if last['close'] <= last['lower_band']:
            long_reasons.append(("Bollinger Lower Band Bounce", 14))
        elif last['close'] >= last['upper_band']:
            short_reasons.append(("Bollinger Upper Band Rejection", 14))

        # Strategy 6: Stochastic Reversal Cross
        if last['stoch_k'] < 25 and last['stoch_k'] > last['stoch_d']:
            long_reasons.append(("Stoch Oversold Turn Up", 12))
        elif last['stoch_k'] > 75 and last['stoch_k'] < last['stoch_d']:
            short_reasons.append(("Stoch Overbought Turn Down", 12))

        # Strategy 7: Volume Breakout Spike
        if pd.notna(last['vol_ma']) and last['volume'] > (last['vol_ma'] * 1.5):
            if last['close'] >= last['open']:
                long_reasons.append(("High Volume Buying Spike", 12))
            else:
                short_reasons.append(("High Volume Selling Spike", 12))

        # Strategy 8: VWAP Position
        if pd.notna(last['vwap']):
            if last['close'] > last['vwap']:
                long_reasons.append(("Price Above VWAP", 6))
            else:
                short_reasons.append(("Price Below VWAP", 6))

        # Strategy 9: Bullish / Bearish Engulfing Pattern
        if last['close'] > last['open'] and prev['close'] < prev['open'] and last['close'] > prev['open']:
            long_reasons.append(("Bullish Engulfing Candle", 12))
        elif last['close'] < last['open'] and prev['close'] > prev['open'] and last['close'] < prev['open']:
            short_reasons.append(("Bearish Engulfing Candle", 12))

        # Strategy 10: Pinbar / Hammer Wick Rejection
        body = abs(last['close'] - last['open'])
        lower_wick = min(last['close'], last['open']) - last['low']
        upper_wick = last['high'] - max(last['close'], last['open'])
        if lower_wick > body * 1.5 and lower_wick > upper_wick:
            long_reasons.append(("Lower Wick Rejection (Hammer)", 10))
        elif upper_wick > body * 1.5 and upper_wick > lower_wick:
            short_reasons.append(("Upper Wick Rejection (Shooting Star)", 10))

        # Strategy 11: Micro Momentum Surge (ROC)
        if last['roc'] > 0.25:
            long_reasons.append((f"Fast Momentum Surge (+{last['roc']:.2f}%)", 10))
        elif last['roc'] < -0.25:
            short_reasons.append((f"Fast Momentum Drop ({last['roc']:.2f}%)", 10))

        # Strategy 12: Raw Candle Action (Guaranteed Fallback Trigger)
        if last['close'] >= last['open']:
            long_reasons.append(("Bullish Candle Close", 4))
        else:
            short_reasons.append(("Bearish Candle Close", 4))

        # --- SCORE & PICK WINNING DIRECTION ---
        long_score = sum(w for _, w in long_reasons)
        short_score = sum(w for _, w in short_reasons)

        if long_score >= short_score:
            direction = "LONG (BUY) 📈"
            winning_reasons = [r for r, _ in sorted(long_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(50 + long_score, 98)
            tp = last['close'] * 1.008  # +0.8% Scalp TP
            sl = last['close'] * 0.995  # -0.5% Scalp SL
        else:
            direction = "SHORT (SELL) 📉"
            winning_reasons = [r for r, _ in sorted(short_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(50 + short_score, 98)
            tp = last['close'] * 0.992  # -0.8% Scalp TP
            sl = last['close'] * 1.005  # +0.5% Scalp SL

        now_pkt = datetime.now(PKT).strftime("%I:%M:%S %p")
        return {
            'symbol': symbol,
            'direction': direction,
            'confidence': int(confidence),
            'price': last['close'],
            'tp': tp,
            'sl': sl,
            'rsi': last['rsi'],
            'time_pkt': now_pkt,
            'reason': ", ".join(winning_reasons[:3])  # Top 3 strongest triggers
        }

    except Exception as e:
        logger.error(f"Error scanning {symbol}: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Aggressive Multi-Strategy Crypto Scanner Online!*\n\n"
        "Use `/scan` to scan 50+ live crypto pairs across 12 micro-strategies "
        "(EMA Cross, RSI, MACD, Bollinger Bands, Stochastic, VWAP, Volume Spikes & Price Action).\n\n"
        "⚡ *Guaranteed live signals on every scan!*"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text(
        f"🔍 *Scanning {len(SYMBOLS)} live pairs across 12 strategies...*\n"
        f"Exchange: `{exchange.id.upper()}`\n"
        "Please wait a few seconds...",
        parse_mode='Markdown'
    )

    # Parallel batch scanning so 50 coins scan in ~4 seconds without freezing Telegram
    found_signals = []
    batch_size = 10
    for i in range(0, len(SYMBOLS), batch_size):
        batch = SYMBOLS[i:i + batch_size]
        tasks = [asyncio.to_thread(calculate_indicators_and_signal, sym) for sym in batch]
        results = await asyncio.gather(*tasks)
        for res in results:
            if res:
                found_signals.append(res)
        await asyncio.sleep(0.1)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not found_signals:
        await update.message.reply_text(
            "⚠️ *Exchange API Connection Error!*\n"
            "Could not fetch live candles from the exchange. Check server logs for IP/API restrictions.",
            parse_mode='Markdown'
        )
        return

    # Sort by highest confidence first
    found_signals.sort(key=lambda x: x['confidence'], reverse=True)

    header = (
        f"✅ *Scan Complete!* Found `{len(found_signals)}` active setups.\n"
        f"🔥 *Top 10 Highest-Confidence Signals:*\n"
    )
    await update.message.reply_text(header, parse_mode='Markdown')

    for sig in found_signals[:10]:
        price_fmt = f"{sig['price']:.6f}" if sig['price'] < 0.1 else f"{sig['price']:.4f}"
        tp_fmt = f"{sig['tp']:.6f}" if sig['tp'] < 0.1 else f"{sig['tp']:.4f}"
        sl_fmt = f"{sig['sl']:.6f}" if sig['sl'] < 0.1 else f"{sig['sl']:.4f}"

        txt = (
            f"🚨 *{sig['symbol']}* — {sig['direction']}\n"
            f"📊 *Confidence:* `{sig['confidence']}%` | *RSI:* `{sig['rsi']:.1f}`\n"
            f"💰 *Entry Price:* `${price_fmt}`\n"
            f"🎯 *Take Profit (Scalp):* `${tp_fmt}`\n"
            f"🛑 *Stop Loss:* `${sl_fmt}`\n"
            f"🧠 *Triggers:* _{sig['reason']}_\n"
            f"⏰ *Time (PKT):* `{sig['time_pkt']}`"
        )
        await update.message.reply_text(txt, parse_mode='Markdown')
        await asyncio.sleep(0.3)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")

def run_bot():
    logger.info("Starting Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    # Sirf tab block kare jab ALLOWED_IDS me waqai koi ID di gayi ho
    if ALLOWED_IDS:
        telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
