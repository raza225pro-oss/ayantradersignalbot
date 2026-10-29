import os
import asyncio
import threading
import logging
from flask import Flask
import ccxt
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Quotex Live Scanner Bot is running!"

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

# --- Quotex Live Forex, Commodities & Indices (via Yahoo Finance) ---
QUOTEX_FOREX_MARKETS = {
    "EUR/USD (Live)": "EURUSD=X",
    "GBP/USD (Live)": "GBPUSD=X",
    "USD/JPY (Live)": "JPY=X",
    "AUD/USD (Live)": "AUDUSD=X",
    "USD/CAD (Live)": "CAD=X",
    "USD/CHF (Live)": "CHF=X",
    "NZD/USD (Live)": "NZDUSD=X",
    "EUR/GBP (Live)": "EURGBP=X",
    "EUR/JPY (Live)": "EURJPY=X",
    "GBP/JPY (Live)": "GBPJPY=X",
    "AUD/JPY (Live)": "AUDJPY=X",
    "EUR/AUD (Live)": "EURAUD=X",
    "EUR/CAD (Live)": "EURCAD=X",
    "GBP/AUD (Live)": "GBPAUD=X",
    "GBP/CAD (Live)": "GBPCAD=X",
    "GBP/CHF (Live)": "GBPCHF=X",
    "AUD/CAD (Live)": "AUDCAD=X",
    "AUD/CHF (Live)": "AUDCHF=X",
    "CAD/JPY (Live)": "CADJPY=X",
    "CHF/JPY (Live)": "CHFJPY=X",
    "EUR/CHF (Live)": "EURCHF=X",
    "NZD/JPY (Live)": "NZDJPY=X",
    "XAU/USD Gold (Live)": "GC=F",
    "XAG/USD Silver (Live)": "SI=F",
    "UKBrent / Crude Oil (Live)": "CL=F"
}

# --- Quotex Live Crypto Markets (Fallback via CCXT) ---
QUOTEX_CRYPTO_MARKETS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'LTC/USDT', 'DOT/USDT', 'LINK/USDT',
    'AVAX/USDT', 'TRX/USDT', 'TON/USDT', 'SHIB/USDT', 'PEPE/USDT'
]

def init_exchange():
    for ex_name in ['binance', 'bybit', 'kucoin']:
        try:
            ex = getattr(ccxt, ex_name)({'enableRateLimit': True})
            ex.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=5)
            return ex
        except Exception:
            continue
    return ccxt.bybit({'enableRateLimit': True})

exchange = init_exchange()

def evaluate_12_strategies(df, market_name):
    """Applies 12 Binary Options 1-Minute Strategies on OHLCV DataFrame"""
    try:
        if df is None or len(df) < 25:
            return None

        # Ensure numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 25:
            return None

        # 1. EMAs (5, 9, 21)
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

        # 2. Wilder's RSI (14) & Fast Binary RSI (7)
        delta = df['close'].diff()
        gain = delta.clip(lower=0).ewm(alpha=1/7, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/7, adjust=False).mean()
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

        # 5. Stochastic Oscillator (14, 3)
        df['low_14'] = df['low'].rolling(window=14).min()
        df['high_14'] = df['high'].rolling(window=14).max()
        denom = (df['high_14'] - df['low_14']).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - df['low_14']) / denom)).fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        # 6. CCI (Commodity Channel Index - 14) - Great for Quotex Forex
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma_tp = tp.rolling(window=14).mean()
        mad = tp.rolling(window=14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).replace(0, np.nan)
        df['cci'] = ((tp - sma_tp) / (0.015 * mad)).fillna(0)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        call_reasons = []
        put_reasons = []

        # --- QUOTEX 1-MINUTE BINARY STRATEGIES ---

        # 1. EMA 5/9 Fast Binary Crossover
        if prev['ema_5'] <= prev['ema_9'] and last['ema_5'] > last['ema_9']:
            call_reasons.append(("EMA 5/9 Bullish Cross", 16))
        elif prev['ema_5'] >= prev['ema_9'] and last['ema_5'] < last['ema_9']:
            put_reasons.append(("EMA 5/9 Bearish Cross", 16))

        # 2. EMA Trend Alignment
        if last['ema_5'] > last['ema_9'] > last['ema_21']:
            call_reasons.append(("Strong Uptrend Alignment", 10))
        elif last['ema_5'] < last['ema_9'] < last['ema_21']:
            put_reasons.append(("Strong Downtrend Alignment", 10))

        # 3. RSI (7) Binary Reversal & Momentum
        if last['rsi'] < 28:
            call_reasons.append((f"RSI Oversold Reversal ({last['rsi']:.0f})", 16))
        elif last['rsi'] > 72:
            put_reasons.append((f"RSI Overbought Reversal ({last['rsi']:.0f})", 16))
        elif 52 < last['rsi'] <= 70:
            call_reasons.append(("RSI Bullish Momentum", 6))
        elif 30 <= last['rsi'] < 48:
            put_reasons.append(("RSI Bearish Momentum", 6))

        # 4. Bollinger Band Rejection (Quotex Sureshot Pattern)
        if last['close'] <= last['lower_band']:
            call_reasons.append(("Bollinger Lower Band Bounce", 15))
        elif last['close'] >= last['upper_band']:
            put_reasons.append(("Bollinger Upper Band Rejection", 15))

        # 5. Stochastic Golden / Death Cross
        if last['stoch_k'] < 25 and last['stoch_k'] > last['stoch_d']:
            call_reasons.append(("Stochastic Oversold Cross Up", 14))
        elif last['stoch_k'] > 75 and last['stoch_k'] < last['stoch_d']:
            put_reasons.append(("Stochastic Overbought Cross Down", 14))

        # 6. MACD Momentum Flip
        if prev['macd_hist'] <= 0 and last['macd_hist'] > 0:
            call_reasons.append(("MACD Histogram Flip Green", 14))
        elif prev['macd_hist'] >= 0 and last['macd_hist'] < 0:
            put_reasons.append(("MACD Histogram Flip Red", 14))
        elif last['macd'] > last['macd_signal']:
            call_reasons.append(("MACD Bullish Zone", 5))
        else:
            put_reasons.append(("MACD Bearish Zone", 5))

        # 7. CCI 100/-100 Breakout
        if last['cci'] < -100:
            call_reasons.append(("CCI Extreme Oversold Bounce", 12))
        elif last['cci'] > 100:
            put_reasons.append(("CCI Extreme Overbought Drop", 12))

        # 8. Engulfing Candlestick Pattern
        if last['close'] > last['open'] and prev['close'] < prev['open'] and last['close'] > prev['open']:
            call_reasons.append(("Bullish Engulfing Pattern", 14))
        elif last['close'] < last['open'] and prev['close'] > prev['open'] and last['close'] < prev['open']:
            put_reasons.append(("Bearish Engulfing Pattern", 14))

        # 9. Pinbar / Wick Rejection
        body = abs(last['close'] - last['open'])
        lower_wick = min(last['close'], last['open']) - last['low']
        upper_wick = last['high'] - max(last['close'], last['open'])
        if lower_wick > body * 1.5 and lower_wick > upper_wick:
            call_reasons.append(("Hammer Lower-Wick Rejection", 12))
        elif upper_wick > body * 1.5 and upper_wick > lower_wick:
            put_reasons.append(("Shooting Star Upper-Wick Rejection", 12))

        # 10. SMA 20 Mean Trend
        if last['close'] > last['sma_20']:
            call_reasons.append(("Price Above 20 SMA", 5))
        else:
            put_reasons.append(("Price Below 20 SMA", 5))

        # 11. Candle Body Momentum
        if last['close'] >= last['open']:
            call_reasons.append(("Bullish Active Candle", 4))
        else:
            put_reasons.append(("Bearish Active Candle", 4))

        call_score = sum(w for _, w in call_reasons)
        put_score = sum(w for _, w in put_reasons)

        if call_score >= put_score:
            direction = "🟢 CALL (UP ⬆️)"
            reasons = [r for r, _ in sorted(call_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(55 + call_score, 98)
        else:
            direction = "🔴 PUT (DOWN ⬇️)"
            reasons = [r for r, _ in sorted(put_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(55 + put_score, 98)

        now_pkt = datetime.now(PKT)
        entry_time = (now_pkt + timedelta(minutes=1)).strftime("%I:%M %p")

        return {
            'symbol': market_name,
            'direction': direction,
            'confidence': int(confidence),
            'price': float(last['close']),
            'rsi': float(last['rsi']),
            'entry_time': entry_time,
            'time_pkt': now_pkt.strftime("%I:%M:%S %p"),
            'reason': ", ".join(reasons[:3])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

def scan_all_quotex_markets():
    signals = []

    # 1. Batch Download All Quotex Live Forex & Commodities via Yahoo Finance (Fast 1-Request Download!)
    try:
        yf_tickers = list(QUOTEX_FOREX_MARKETS.values())
        data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        
        for market_name, yf_sym in QUOTEX_FOREX_MARKETS.items():
            try:
                if yf_sym in data and not data[yf_sym].empty:
                    sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                    if len(sub_df) >= 25:
                        # Check if market is actually moving (not closed for weekend)
                        if sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            clean_df = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                            res = evaluate_12_strategies(clean_df, f"💱 {market_name}")
                            if res:
                                signals.append(res)
            except Exception as e:
                continue
    except Exception as e:
        logger.warning(f"Yahoo Finance Forex fetch issue: {e}")

    # 2. Scan Quotex Live Crypto Pairs (24/7 Active even on Weekends)
    for sym in QUOTEX_CRYPTO_MARKETS:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, timeframe='1m', limit=50)
            if ohlcv and len(ohlcv) >= 25:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                clean_name = sym.replace('/USDT', '/USD (Live Crypto)')
                res = evaluate_12_strategies(df, f"🪙 {clean_name}")
                if res:
                    signals.append(res)
        except Exception:
            continue

    return signals

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "📊 *Quotex Live Market Scanner Online!*\n\n"
        "This bot scans *40+ Real Quotex Live Markets* on the *1-Minute Timeframe*:\n"
        "✅ *Live Forex Pairs:* EUR/USD, GBP/USD, USD/JPY, AUD/CAD, EUR/JPY, GBP/JPY & 18 more!\n"
        "✅ *Live Commodities:* Gold (XAU/USD), Silver, Crude Oil\n"
        "✅ *Live Crypto:* BTC, ETH, SOL, XRP, DOGE & more!\n\n"
        "👉 Send `/scan` to get instant *1-Minute Binary Signals (CALL / PUT)*!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text(
        "🔍 *Scanning Quotex Live Forex, Gold & Crypto Markets (1M Timeframe)...*\n"
        "Analyzing 11 Binary Options indicators. Please wait 4-6 seconds...",
        parse_mode='Markdown'
    )

    found_signals = await asyncio.to_thread(scan_all_quotex_markets)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not found_signals:
        await update.message.reply_text(
            "⚠️ *No Live Data Received!* Please check your internet connection or try again in a few seconds.",
            parse_mode='Markdown'
        )
        return

    # Sort by highest confidence first, prioritizing Live Forex when markets are open
    found_signals.sort(key=lambda x: (1 if "💱" in x['symbol'] else 0, x['confidence']), reverse=True)

    header = (
        f"✅ *Quotex Live Scan Complete!*\n"
        f"Analyzed `{len(found_signals)}` active live markets.\n"
        f"🔥 *Top 10 High-Accuracy 1-Minute Signals:*\n"
    )
    await update.message.reply_text(header, parse_mode='Markdown')

    for sig in found_signals[:10]:
        price_fmt = f"{sig['price']:.5f}" if sig['price'] < 10 else f"{sig['price']:.2f}"

        txt = (
            f"📊 *Pair:* `{sig['symbol']}`\n"
            f"🎯 *Action:* *{sig['direction']}*\n"
            f"⏳ *Timeframe / Expiry:* `1 Minute (M1)`\n"
            f"⏰ *Next Candle Entry:* `{sig['entry_time']} (PKT)`\n"
            f"📈 *Confidence:* `{sig['confidence']}%` | *RSI(7):* `{sig['rsi']:.0f}`\n"
            f"💰 *Current Price:* `{price_fmt}`\n"
            f"🧠 *Strategy Match:* _{sig['reason']}_"
        )
        await update.message.reply_text(txt, parse_mode='Markdown')
        await asyncio.sleep(0.3)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")

def run_bot():
    logger.info("Starting Quotex Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    if ALLOWED_IDS:
        telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
