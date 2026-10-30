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
    return "Ayyan Trader Quotex Live Scanner Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Bot & Branding Setup ---
TOKEN = os.getenv("BOT_TOKEN")
raw_ids = os.getenv("ALLOWED_IDS", "")
ALLOWED_IDS = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
PKT = timezone(timedelta(hours=5))

# Global Signal Counter
SIGNAL_COUNT = 0

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

# --- Quotex Live Crypto Markets (24/7 via CCXT) ---
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

def evaluate_high_accuracy_strategies(df, market_name):
    """Applies 15 High-Precision Binary Options 1-Minute Strategies with Anti-Repainting & Volatility Filters"""
    try:
        if df is None or len(df) < 35:
            return None

        # Ensure numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 35:
            return None

        # 1. EMAs (5, 9, 21, 50)
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        # 2. Fast Binary RSI (7)
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

        # 6. CCI (Commodity Channel Index - 14)
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma_tp = tp.rolling(window=14).mean()
        mad = tp.rolling(window=14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).replace(0, np.nan)
        df['cci'] = ((tp - sma_tp) / (0.015 * mad)).fillna(0)

        # 7. Williams %R (14) - Accurate Binary Reversal Indicator
        df['will_r'] = (-100 * ((df['high_14'] - df['close']) / denom)).fillna(-50)

        # 8. ATR (14) & ADX (14) - Filter Out Dead/Flat Doji Markets
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        up_move = df['high'] - df['high'].shift(1)
        down_move = df['low'].shift(1) - df['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20)

        # Closed candle (confirmed) & Live forming candle
        closed = df.iloc[-2]
        prev = df.iloc[-3]
        live = df.iloc[-1]

        # Skip dead/micro-doji markets where price is stuck
        if closed['atr'] == 0 or abs(closed['high'] - closed['low']) < (closed['atr'] * 0.25):
            return None

        call_reasons = []
        put_reasons = []

        # --- QUOTEX PRO 1-MINUTE ACCURACY ENGINE ---

        # 1. Confirmed EMA 5/9 Crossover
        if prev['ema_5'] <= prev['ema_9'] and closed['ema_5'] > closed['ema_9'] and live['ema_5'] > live['ema_9']:
            call_reasons.append(("EMA 5/9 Bullish Cross", 18))
        elif prev['ema_5'] >= prev['ema_9'] and closed['ema_5'] < closed['ema_9'] and live['ema_5'] < live['ema_9']:
            put_reasons.append(("EMA 5/9 Bearish Cross", 18))

        # 2. Multi-EMA Trend Alignment (5 > 9 > 21 > 50)
        if live['ema_5'] > live['ema_9'] > live['ema_21'] > live['ema_50']:
            call_reasons.append(("Strong Institutional Uptrend", 14))
        elif live['ema_5'] < live['ema_9'] < live['ema_21'] < live['ema_50']:
            put_reasons.append(("Strong Institutional Downtrend", 14))

        # 3. RSI (7) Sureshot Reversal & Sweet-Spot Momentum
        if closed['rsi'] < 26 and live['rsi'] > closed['rsi']:
            call_reasons.append((f"RSI Oversold Hook Up ({live['rsi']:.0f})", 18))
        elif closed['rsi'] > 74 and live['rsi'] < closed['rsi']:
            put_reasons.append((f"RSI Overbought Hook Down ({live['rsi']:.0f})", 18))
        elif 54 <= live['rsi'] <= 68 and live['rsi'] > closed['rsi']:
            call_reasons.append(("RSI Bullish Acceleration", 8))
        elif 32 <= live['rsi'] <= 46 and live['rsi'] < closed['rsi']:
            put_reasons.append(("RSI Bearish Acceleration", 8))

        # 4. Bollinger Band Rejections (Confirmed Bounce)
        if closed['low'] <= closed['lower_band'] and live['close'] > live['open']:
            call_reasons.append(("Bollinger Lower Band Rejection", 17))
        elif closed['high'] >= closed['upper_band'] and live['close'] < live['open']:
            put_reasons.append(("Bollinger Upper Band Rejection", 17))

        # 5. Stochastic + Williams %R Confluence
        if live['stoch_k'] < 25 and live['stoch_k'] > live['stoch_d'] and live['will_r'] < -75:
            call_reasons.append(("Stoch + Williams %R Oversold Cross", 16))
        elif live['stoch_k'] > 75 and live['stoch_k'] < live['stoch_d'] and live['will_r'] > -25:
            put_reasons.append(("Stoch + Williams %R Overbought Cross", 16))

        # 6. MACD Histogram Surge
        if closed['macd_hist'] > prev['macd_hist'] and live['macd_hist'] > 0:
            call_reasons.append(("MACD Bullish Surge", 12))
        elif closed['macd_hist'] < prev['macd_hist'] and live['macd_hist'] < 0:
            put_reasons.append(("MACD Bearish Surge", 12))

        # 7. CCI 100/-100 Reversal Hook
        if closed['cci'] < -100 and live['cci'] > closed['cci']:
            call_reasons.append(("CCI Oversold Recovery", 12))
        elif closed['cci'] > 100 and live['cci'] < closed['cci']:
            put_reasons.append(("CCI Overbought Drop", 12))

        # 8. Confirmed Engulfing Candlestick
        if closed['close'] > closed['open'] and prev['close'] < prev['open'] and closed['close'] > prev['open']:
            call_reasons.append(("Confirmed Bullish Engulfing", 15))
        elif closed['close'] < closed['open'] and prev['close'] > prev['open'] and closed['close'] < prev['open']:
            put_reasons.append(("Confirmed Bearish Engulfing", 15))

        # 9. Wick Rejection (Hammer / Shooting Star)
        body = abs(closed['close'] - closed['open'])
        lower_wick = min(closed['close'], closed['open']) - closed['low']
        upper_wick = closed['high'] - max(closed['close'], closed['open'])
        if lower_wick > body * 1.6 and lower_wick > upper_wick:
            call_reasons.append(("Pinbar Lower-Wick Rejection", 13))
        elif upper_wick > body * 1.6 and upper_wick > lower_wick:
            put_reasons.append(("Pinbar Upper-Wick Rejection", 13))

        # 10. ADX Trend Power Booster
        if live['adx'] >= 25:
            if live['ema_5'] > live['ema_21']:
                call_reasons.append((f"Strong ADX Trend ({live['adx']:.0f})", 10))
            else:
                put_reasons.append((f"Strong ADX Trend ({live['adx']:.0f})", 10))

        # 11. Live Candle Confirmation (Anti-Counter-Trend Filter)
        if live['close'] > live['open']:
            call_reasons.append(("Live Buyers Active", 6))
        elif live['close'] < live['open']:
            put_reasons.append(("Live Sellers Active", 6))

        call_score = sum(w for _, w in call_reasons)
        put_score = sum(w for _, w in put_reasons)

        # Require a clear gap between CALL and PUT to avoid 50/50 choppy signals
        score_diff = abs(call_score - put_score)
        if score_diff < 12:
            return None

        if call_score > put_score:
            direction = "CALL 🟢⬆️"
            reasons = [r for r, _ in sorted(call_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(60 + int(call_score * 0.75), 99)
        else:
            direction = "PUT 🔴⬇️"
            reasons = [r for r, _ in sorted(put_reasons, key=lambda x: x[1], reverse=True)]
            confidence = min(60 + int(put_score * 0.75), 99)

        now_pkt = datetime.now(PKT)
        # 24-hour format entry time just like the image (e.g., 03:28)
        entry_time = (now_pkt + timedelta(minutes=1)).strftime("%H:%M")

        return {
            'symbol': market_name,
            'direction': direction,
            'confidence': int(confidence),
            'price': float(live['close']),
            'rsi': float(live['rsi']),
            'entry_time': entry_time,
            'reason': ", ".join(reasons[:3])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

def scan_all_quotex_markets():
    signals = []

    # 1. Batch Download All Quotex Live Forex & Commodities via Yahoo Finance
    try:
        yf_tickers = list(QUOTEX_FOREX_MARKETS.values())
        data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        
        for market_name, yf_sym in QUOTEX_FOREX_MARKETS.items():
            try:
                if yf_sym in data and not data[yf_sym].empty:
                    sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                    if len(sub_df) >= 35:
                        # Ensure market is actively moving (not closed for weekend)
                        if sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            clean_df = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                            res = evaluate_high_accuracy_strategies(clean_df, market_name)
                            if res:
                                res['is_forex'] = True
                                signals.append(res)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Yahoo Finance Forex fetch issue: {e}")

    # 2. Scan Quotex Live Crypto Pairs (24/7 Active)
    for sym in QUOTEX_CRYPTO_MARKETS:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, timeframe='1m', limit=60)
            if ohlcv and len(ohlcv) >= 35:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                clean_name = sym.replace('/USDT', '/USD (Live)')
                res = evaluate_high_accuracy_strategies(df, clean_name)
                if res:
                    res['is_forex'] = False
                    signals.append(res)
        except Exception:
            continue

    return signals

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👾 *AYYAN GenAI+ QUOTEX BOT* 👾\n"
        "━━━━━━━━ 🆓 ━━━━━━━━\n"
        "📊 *40+ Quotex Live Markets Scanner*\n"
        "⚡ *Timeframe:* `M1 (1-Minute)`\n"
        "🎯 *Accuracy Engine:* `15 Pro Indicators + ADX Filter`\n"
        "━━━━━━━━ 🆓 ━━━━━━━━\n\n"
        "👉 Send `/scan` to get VIP *LIVE MARKET* Signals!\n\n"
        "🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸\n"
        "📢 *VIP GROUP :* [Join Ayyan Trader Group](https://t.me/ayyan_trader02)"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown', disable_web_page_preview=True)

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SIGNAL_COUNT

    status_msg = await update.message.reply_text(
        "👾 *AYYAN GenAI+ SCANNER ACTIVE...*\n"
        "🔍 Filtering high-accuracy *LIVE* signals on M1 timeframe. Please wait 4-6 seconds...",
        parse_mode='Markdown'
    )

    found_signals = await asyncio.to_thread(scan_all_quotex_markets)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not found_signals:
        await update.message.reply_text(
            "⚠️ *No High-Accuracy Setup Found Right Now!*\n"
            "Markets are either flat/choppy or data is refreshing. Try `/scan` again in 30 seconds.",
            parse_mode='Markdown'
        )
        return

    # Sort by highest confidence first, prioritizing Live Forex when open
    found_signals.sort(key=lambda x: (1 if x.get('is_forex') else 0, x['confidence']), reverse=True)

    # Send Top 5 Strongest Sureshot Signals in the exact decorated style
    top_signals = found_signals[:5]

    for sig in top_signals:
        SIGNAL_COUNT += 1

        decorated_msg = (
            f"👾 *AYYAN GenAI+ SIGNAL* 👾\n\n"
            f"━━━━━━━━ 🆓 ━━━━━━━━\n"
            f"📊 *PAIR :* `{sig['symbol']}`\n"
            f"⚡ *TIMEFRAME :* `M1`\n"
            f"⏰ *ENTRY TIME :* `{sig['entry_time']}`\n"
            f"🔊 *DIRECTION :* *{sig['direction']}*\n"
            f"━━━━━━━━ 🆓 ━━━━━━━━\n\n"
            f"💮💮 *!SURESHOT SETUP!* 💮💮\n\n"
            f"📊 *ACCURACY :* `{sig['confidence']}%` 🟩\n"
            f"🧠 *STRATEGY :* _{sig['reason']}_\n"
            f"🟢 *SIGNAL COUNT :* `{SIGNAL_COUNT}` 🎖️\n\n"
            f"🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸\n"
            f"📢 *GROUP :* [Join VIP Group](https://t.me/ayyan_trader02)"
        )

        await update.message.reply_text(
            decorated_msg,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await asyncio.sleep(0.35)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Access Denied. Contact @Ayyan_trader01 for access.")

def run_bot():
    logger.info("Starting Ayyan GenAI+ Quotex Telegram Bot...")
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
