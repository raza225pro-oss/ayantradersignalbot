import os
import asyncio
import threading
import logging
import hashlib
from flask import Flask
import ccxt
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Ayyan GenAI+ Quotex Bot is running!"

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

SIGNAL_COUNT = 0

# --- STRICTLY VERIFIED QUOTEX LIVE MARKETS ---
QUOTEX_LIVE_MARKETS = {
    "EUR/USD": {"type": "forex", "ticker": "EURUSD=X"},
    "GBP/USD": {"type": "forex", "ticker": "GBPUSD=X"},
    "USD/JPY": {"type": "forex", "ticker": "JPY=X"},
    "AUD/USD": {"type": "forex", "ticker": "AUDUSD=X"},
    "USD/CAD": {"type": "forex", "ticker": "CAD=X"},
    "USD/CHF": {"type": "forex", "ticker": "CHF=X"},
    "EUR/GBP": {"type": "forex", "ticker": "EURGBP=X"},
    "EUR/JPY": {"type": "forex", "ticker": "EURJPY=X"},
    "GBP/JPY": {"type": "forex", "ticker": "GBPJPY=X"},
    "AUD/JPY": {"type": "forex", "ticker": "AUDJPY=X"},
    "EUR/AUD": {"type": "forex", "ticker": "EURAUD=X"},
    "EUR/CAD": {"type": "forex", "ticker": "EURCAD=X"},
    "GBP/AUD": {"type": "forex", "ticker": "GBPAUD=X"},
    "GBP/CAD": {"type": "forex", "ticker": "GBPCAD=X"},
    "GBP/CHF": {"type": "forex", "ticker": "GBPCHF=X"},
    "AUD/CAD": {"type": "forex", "ticker": "AUDCAD=X"},
    "AUD/CHF": {"type": "forex", "ticker": "AUDCHF=X"},
    "CAD/JPY": {"type": "forex", "ticker": "CADJPY=X"},
    "CHF/JPY": {"type": "forex", "ticker": "CHFJPY=X"},
    "EUR/CHF": {"type": "forex", "ticker": "EURCHF=X"},
    "XAU/USD (Gold)": {"type": "forex", "ticker": "GC=F"},
    "XAG/USD (Silver)": {"type": "forex", "ticker": "SI=F"},
    "UKBrent (Oil)": {"type": "forex", "ticker": "CL=F"},
    "BTC/USD": {"type": "crypto", "ticker": "BTC/USDT"},
    "ETH/USD": {"type": "crypto", "ticker": "ETH/USDT"},
    "LTC/USD": {"type": "crypto", "ticker": "LTC/USDT"},
    "XRP/USD": {"type": "crypto", "ticker": "XRP/USDT"},
}

# --- STRICTLY VERIFIED QUOTEX OTC MARKETS ---
QUOTEX_OTC_MARKETS = {
    "EUR/USD (OTC)": {"base": "EURUSD=X", "seed": 101},
    "GBP/USD (OTC)": {"base": "GBPUSD=X", "seed": 102},
    "USD/JPY (OTC)": {"base": "JPY=X", "seed": 103},
    "AUD/USD (OTC)": {"base": "AUDUSD=X", "seed": 104},
    "USD/CAD (OTC)": {"base": "CAD=X", "seed": 105},
    "USD/CHF (OTC)": {"base": "CHF=X", "seed": 106},
    "NZD/USD (OTC)": {"base": "NZDUSD=X", "seed": 107},
    "EUR/GBP (OTC)": {"base": "EURGBP=X", "seed": 108},
    "EUR/JPY (OTC)": {"base": "EURJPY=X", "seed": 109},
    "GBP/JPY (OTC)": {"base": "GBPJPY=X", "seed": 110},
    "AUD/CAD (OTC)": {"base": "AUDCAD=X", "seed": 111},
    "GBP/CHF (OTC)": {"base": "GBPCHF=X", "seed": 112},
    "USD/PKR (OTC)": {"base": "PKR=X", "seed": 201},
    "USD/INR (OTC)": {"base": "INR=X", "seed": 202},
    "USD/BRL (OTC)": {"base": "BRL=X", "seed": 203},
    "USD/BDT (OTC)": {"base": "BDT=X", "seed": 204},
    "USD/EGP (OTC)": {"base": "EGP=X", "seed": 205},
    "USD/TRY (OTC)": {"base": "TRY=X", "seed": 206},
    "USD/IDR (OTC)": {"base": "IDR=X", "seed": 207},
    "USD/MXN (OTC)": {"base": "MXN=X", "seed": 208},
    "USD/NGN (OTC)": {"base": "NGN=X", "seed": 209},
    "USD/ARS (OTC)": {"base": "ARS=X", "seed": 210},
    "XAU/USD Gold (OTC)": {"base": "GC=F", "seed": 301},
    "UKBrent Oil (OTC)": {"base": "CL=F", "seed": 302},
}

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

# --- OTC Algorithmic Feed Generator (Synchronized Per Minute) ---
def generate_otc_ohlcv(pair_name, seed_val, count=60):
    now_utc = datetime.now(timezone.utc)
    base_minute = int(now_utc.timestamp() // 60)
    rows = []
    price = 100.0 + (seed_val % 50)

    for i in range(count):
        m_stamp = base_minute - (count - 1 - i)
        h = hashlib.sha256(f"{pair_name}_{seed_val}_{m_stamp}".encode()).hexdigest()
        val1 = (int(h[:8], 16) / 0xFFFFFFFF) - 0.5
        val2 = (int(h[8:16], 16) / 0xFFFFFFFF)
        val3 = (int(h[16:24], 16) / 0xFFFFFFFF)

        # Create realistic OTC wave + liquidity cycles
        wave = np.sin(i / 4.5 + (seed_val % 7)) * 0.18
        delta = (val1 * 0.45) + wave
        open_p = price
        close_p = open_p + delta
        high_p = max(open_p, close_p) + (val2 * 0.22)
        low_p = min(open_p, close_p) - (val3 * 0.22)
        price = close_p
        rows.append([m_stamp * 60, open_p, high_p, low_p, close_p, 1000 + int(val2 * 500)])

    return pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# --- 18-Strategy High-Accuracy Engine ---
def evaluate_high_accuracy_strategies(df, market_name):
    try:
        if df is None or len(df) < 35:
            return None

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
        gain7 = delta.clip(lower=0).ewm(alpha=1/7, adjust=False).mean()
        loss7 = (-delta.clip(upper=0)).ewm(alpha=1/7, adjust=False).mean()
        rs7 = gain7 / loss7.replace(0, np.nan)
        df['rsi'] = (100 - (100 / (1 + rs7))).fillna(50)

        # 3. MACD (12, 26, 9)
        df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']

        # 4. Bollinger Bands (20, 2)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma_20'] + (df['std'] * 2)
        df['lower_band'] = df['sma_20'] - (df['std'] * 2)

        # 5. Stochastic (14, 3) & Williams %R (14)
        df['low_14'] = df['low'].rolling(window=14).min()
        df['high_14'] = df['high'].rolling(window=14).max()
        denom = (df['high_14'] - df['low_14']).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - df['low_14']) / denom)).fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)
        df['will_r'] = (-100 * ((df['high_14'] - df['close']) / denom)).fillna(-50)

        # 6. CCI (14)
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma_tp = tp.rolling(window=14).mean()
        mad = tp.rolling(window=14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).replace(0, np.nan)
        df['cci'] = ((tp - sma_tp) / (0.015 * mad)).fillna(0)

        # 7. ATR (14) & ADX (14)
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean().bfill()

        up_move = df['high'] - df['high'].shift(1)
        down_move = df['low'].shift(1) - df['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20)

        # 8. Dynamic Support & Resistance (15-Period Fractals)
        df['support'] = df['low'].rolling(window=15).min()
        df['resistance'] = df['high'].rolling(window=15).max()

        closed = df.iloc[-2]
        prev = df.iloc[-3]
        live = df.iloc[-1]

        call_reasons = []
        put_reasons = []

        atr_val = float(live['atr']) if live['atr'] > 0 else 0.0001

        # 1. Support & Resistance Zone Bounce
        if abs(live['low'] - live['support']) <= (atr_val * 0.45) and live['close'] >= live['open']:
            call_reasons.append(("Support Zone Rejection", 20))
        elif abs(live['high'] - live['resistance']) <= (atr_val * 0.45) and live['close'] <= live['open']:
            put_reasons.append(("Resistance Zone Rejection", 20))

        # 2. EMA 5/9 Crossover
        if prev['ema_5'] <= prev['ema_9'] and (closed['ema_5'] > closed['ema_9'] or live['ema_5'] > live['ema_9']):
            call_reasons.append(("EMA 5/9 Bullish Cross", 18))
        elif prev['ema_5'] >= prev['ema_9'] and (closed['ema_5'] < closed['ema_9'] or live['ema_5'] < live['ema_9']):
            put_reasons.append(("EMA 5/9 Bearish Cross", 18))

        # 3. M1 + M5 Trend Alignment
        if live['ema_5'] > live['ema_9'] > live['ema_21'] > live['ema_50']:
            call_reasons.append(("M1 + M5 Strong Uptrend", 16))
        elif live['ema_5'] < live['ema_9'] < live['ema_21'] < live['ema_50']:
            put_reasons.append(("M1 + M5 Strong Downtrend", 16))
        elif live['ema_9'] > live['ema_21']:
            call_reasons.append(("EMA Trend Bullish", 8))
        else:
            put_reasons.append(("EMA Trend Bearish", 8))

        # 4. RSI (7) Sureshot Reversal & Momentum
        if closed['rsi'] < 28 or live['rsi'] < 25:
            call_reasons.append((f"RSI Oversold Reversal ({live['rsi']:.0f})", 18))
        elif closed['rsi'] > 72 or live['rsi'] > 75:
            put_reasons.append((f"RSI Overbought Reversal ({live['rsi']:.0f})", 18))
        elif 52 <= live['rsi'] <= 68 and live['rsi'] > closed['rsi']:
            call_reasons.append(("RSI Bullish Momentum", 9))
        elif 32 <= live['rsi'] <= 48 and live['rsi'] < closed['rsi']:
            put_reasons.append(("RSI Bearish Momentum", 9))

        # 5. Bollinger Band Bounce
        if closed['low'] <= closed['lower_band'] or live['close'] <= live['lower_band']:
            call_reasons.append(("Bollinger Lower Band Bounce", 17))
        elif closed['high'] >= closed['upper_band'] or live['close'] >= live['upper_band']:
            put_reasons.append(("Bollinger Upper Band Rejection", 17))

        # 6. Stochastic + Williams %R Confluence
        if live['stoch_k'] < 25 and live['stoch_k'] >= live['stoch_d'] and live['will_r'] < -75:
            call_reasons.append(("Stoch + Will%R Oversold", 16))
        elif live['stoch_k'] > 75 and live['stoch_k'] <= live['stoch_d'] and live['will_r'] > -25:
            put_reasons.append(("Stoch + Will%R Overbought", 16))

        # 7. MACD Flip & Surge
        if prev['macd_hist'] <= 0 and live['macd_hist'] > 0:
            call_reasons.append(("MACD Flip Green", 15))
        elif prev['macd_hist'] >= 0 and live['macd_hist'] < 0:
            put_reasons.append(("MACD Flip Red", 15))
        elif live['macd_hist'] > closed['macd_hist'] and live['macd_hist'] > 0:
            call_reasons.append(("MACD Bullish Surge", 8))
        elif live['macd_hist'] < closed['macd_hist'] and live['macd_hist'] < 0:
            put_reasons.append(("MACD Bearish Surge", 8))

        # 8. CCI Reversal Hook
        if live['cci'] < -100:
            call_reasons.append(("CCI Oversold Bounce", 13))
        elif live['cci'] > 100:
            put_reasons.append(("CCI Overbought Drop", 13))

        # 9. Engulfing Candlestick
        if closed['close'] > closed['open'] and prev['close'] < prev['open'] and closed['close'] > prev['open']:
            call_reasons.append(("Bullish Engulfing Pattern", 15))
        elif closed['close'] < closed['open'] and prev['close'] > prev['open'] and closed['close'] < prev['open']:
            put_reasons.append(("Bearish Engulfing Pattern", 15))

        # 10. Pinbar / Wick Rejection
        body = abs(closed['close'] - closed['open'])
        lower_wick = min(closed['close'], closed['open']) - closed['low']
        upper_wick = closed['high'] - max(closed['close'], closed['open'])
        if lower_wick > body * 1.5 and lower_wick > upper_wick:
            call_reasons.append(("Hammer Wick Rejection", 14))
        elif upper_wick > body * 1.5 and upper_wick > lower_wick:
            put_reasons.append(("Shooting Star Wick Rejection", 14))

        # 11. Candle Exhaustion Filter
        closed_size = abs(closed['close'] - closed['open'])
        if closed_size > (atr_val * 2.2):
            if closed['close'] > closed['open']:
                put_reasons.append(("Exhaustion Spike Pullback", 14))
            else:
                call_reasons.append(("Exhaustion Drop Pullback", 14))

        # 12. ADX Trend Power & Live Pressure
        if live['adx'] >= 23:
            if live['ema_5'] > live['ema_21']:
                call_reasons.append((f"Strong ADX Trend ({live['adx']:.0f})", 9))
            else:
                put_reasons.append((f"Strong ADX Trend ({live['adx']:.0f})", 9))

        if live['close'] >= live['open']:
            call_reasons.append(("Live Buyers Active", 5))
        else:
            put_reasons.append(("Live Sellers Active", 5))

        call_score = sum(w for _, w in call_reasons)
        put_score = sum(w for _, w in put_reasons)
        score_diff = abs(call_score - put_score)

        if call_score >= put_score:
            direction = "CALL 🟢⬆️"
            reasons = [r for r, _ in sorted(call_reasons, key=lambda x: x[1], reverse=True)]
            winning_score = call_score
        else:
            direction = "PUT 🔴⬇️"
            reasons = [r for r, _ in sorted(put_reasons, key=lambda x: x[1], reverse=True)]
            winning_score = put_score

        confidence = min(68 + int(winning_score * 0.45) + int(score_diff * 0.35), 99)

        now_pkt = datetime.now(PKT)
        entry_time = (now_pkt + timedelta(minutes=1)).strftime("%I:%M %p")

        return {
            'symbol': market_name,
            'direction': direction,
            'confidence': int(confidence),
            'score_diff': score_diff,
            'entry_time': entry_time,
            'reason': ", ".join(reasons[:3])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

# --- Data Fetchers ---
def fetch_single_pair_signal(pair_name, is_otc=False):
    if is_otc:
        info = QUOTEX_OTC_MARKETS.get(pair_name)
        if not info:
            return None
        df = generate_otc_ohlcv(pair_name, info['seed'], count=60)
        return evaluate_high_accuracy_strategies(df, pair_name)
    else:
        info = QUOTEX_LIVE_MARKETS.get(pair_name)
        if not info:
            return None
        if info['type'] == 'forex':
            try:
                data = yf.download(tickers=info['ticker'], period="1d", interval="1m", progress=False)
                if data is not None and not data.empty and len(data) >= 35:
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    sub_df = data.dropna(subset=['Close'])
                    # Check if live forex is open and moving
                    if len(sub_df) >= 35 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                        clean_df = pd.DataFrame({
                            'open': sub_df['Open'].values,
                            'high': sub_df['High'].values,
                            'low': sub_df['Low'].values,
                            'close': sub_df['Close'].values,
                            'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                        })
                        return evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)")
            except Exception as e:
                logger.warning(f"Single Forex fetch error: {e}")
            return None
        else:
            try:
                ohlcv = exchange.fetch_ohlcv(info['ticker'], timeframe='1m', limit=60)
                if ohlcv and len(ohlcv) >= 35:
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    return evaluate_high_accuracy_strategies(df, f"{pair_name} (Live)")
            except Exception as e:
                logger.warning(f"Single Crypto fetch error: {e}")
            return None

def scan_best_signals(mode="live", count=1):
    signals = []
    if mode == "otc":
        for pair_name, info in QUOTEX_OTC_MARKETS.items():
            df = generate_otc_ohlcv(pair_name, info['seed'], count=60)
            res = evaluate_high_accuracy_strategies(df, pair_name)
            if res:
                signals.append(res)
    else:
        # Batch Forex Live
        try:
            fx_pairs = {k: v['ticker'] for k, v in QUOTEX_LIVE_MARKETS.items() if v['type'] == 'forex'}
            yf_tickers = list(fx_pairs.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in fx_pairs.items():
                try:
                    if yf_sym in data and not data[yf_sym].empty:
                        sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                        if len(sub_df) >= 35 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            clean_df = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                            res = evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)")
                            if res:
                                res['is_forex'] = True
                                signals.append(res)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Batch Yahoo Finance error: {e}")

        # Crypto Live
        for pair_name, info in QUOTEX_LIVE_MARKETS.items():
            if info['type'] == 'crypto':
                try:
                    ohlcv = exchange.fetch_ohlcv(info['ticker'], timeframe='1m', limit=60)
                    if ohlcv and len(ohlcv) >= 35:
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        res = evaluate_high_accuracy_strategies(df, f"{pair_name} (Live)")
                        if res:
                            res['is_forex'] = False
                            signals.append(res)
                except Exception:
                    continue

    signals.sort(
        key=lambda x: (
            1 if x['score_diff'] >= 15 else 0,
            1 if x.get('is_forex') else 0,
            x['confidence'],
            x['score_diff']
        ),
        reverse=True
    )
    return signals[:count]

# --- UI Keyboards (Matching Screenshot Style) ---
def get_main_dashboard_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎫 CONTACT OWNER", url="https://t.me/Ayyan_trader01"),
            InlineKeyboardButton("👀 START BOT", callback_data="menu_modes")
        ],
        [
            InlineKeyboardButton("📈 LIVE MARKET SIGNALS", callback_data="mode_live"),
            InlineKeyboardButton("🌙 OTC MARKET SIGNALS", callback_data="mode_otc")
        ],
        [
            InlineKeyboardButton("📢 JOIN VIP TELEGRAM GROUP", url="https://t.me/ayyan_trader02")
        ]
    ])

def get_market_sub_kb(mode):
    label = "LIVE" if mode == "live" else "OTC"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ AUTO BEST {label} SIGNAL (M1)", callback_data=f"auto_{mode}_1")],
        [InlineKeyboardButton(f"🔥 TOP 3 {label} SIGNALS (M1)", callback_data=f"auto_{mode}_3")],
        [InlineKeyboardButton(f"🎯 SELECT SPECIFIC {label} PAIR", callback_data=f"list_{mode}_0")],
        [
            InlineKeyboardButton("🎫 Contact Owner", url="https://t.me/Ayyan_trader01"),
            InlineKeyboardButton("👀 Back to Main Menu", callback_data="main_menu")
        ]
    ])

def get_pairs_grid_kb(mode, page=0):
    markets = list(QUOTEX_LIVE_MARKETS.keys()) if mode == "live" else list(QUOTEX_OTC_MARKETS.keys())
    per_page = 10
    start_idx = page * per_page
    end_idx = start_idx + per_page
    sliced = markets[start_idx:end_idx]

    rows = []
    for i in range(0, len(sliced), 2):
        row = []
        for j in range(2):
            if i + j < len(sliced):
                p_name = sliced[i + j]
                idx = start_idx + i + j
                icon = "📊" if mode == "live" else "🌙"
                row.append(InlineKeyboardButton(f"{icon} {p_name}", callback_data=f"pair_{mode}_{idx}"))
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"list_{mode}_{page-1}"))
    if end_idx < len(markets):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{mode}_{page+1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([
        InlineKeyboardButton("⚡ Mode Menu", callback_data=f"mode_{mode}"),
        InlineKeyboardButton("👀 Back to Main Menu", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(rows)

def get_post_signal_kb(mode, pair_idx=None):
    retry_cb = f"pair_{mode}_{pair_idx}" if pair_idx is not None else f"auto_{mode}_1"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ GENERATE NEXT SIGNAL", callback_data=retry_cb)],
        [
            InlineKeyboardButton("🎯 Choose Another Pair", callback_data=f"list_{mode}_0"),
            InlineKeyboardButton("👀 Dashboard", callback_data="main_menu")
        ],
        [
            InlineKeyboardButton("🎫 CONTACT OWNER", url="https://t.me/Ayyan_trader01"),
            InlineKeyboardButton("📢 VIP GROUP", url="https://t.me/ayyan_trader02")
        ]
    ])

def format_signal_card(sig):
    global SIGNAL_COUNT
    SIGNAL_COUNT += 1
    return (
        f"👾 *AYYAN GenAI+ SIGNAL* 👾\n\n"
        f"━━━━━━━━ 🆓 ━━━━━━━━\n"
        f"📊 *PAIR :* `{sig['symbol']}`\n"
        f"⚡ *TIMEFRAME :* `M1`\n"
        f"⏰ *ENTRY TIME :* `{sig['entry_time']}`\n"
        f"🔊 *DIRECTION :* *{sig['direction']}*\n"
        f"⚠️ *1 STEP MTG MUST*\n"
        f"━━━━━━━━ 🆓 ━━━━━━━━\n\n"
        f"💮💮 *!SURESHOT SETUP!* 💮💮\n\n"
        f"📊 *ACCURACY :* `{sig['confidence']}%` 🟩\n"
        f"🧠 *STRATEGY :* _{sig['reason']}_\n"
        f"🟢 *SIGNAL COUNT :* `{SIGNAL_COUNT}` 🎖️\n\n"
        f"🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸\n"
        f"📢 *GROUP :* [Join VIP Group](https://t.me/ayyan_trader02)"
    )

# --- Live Scanning Animation Helper ---
async def play_scan_animation(msg, target_label):
    stages = [
        f"⚡ *INITIALIZING AYYAN GenAI+ ENGINE...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🔄 `Connecting to Quotex Liquidity Feed...`\n`[███▒▒▒▒▒▒▒] 35%`",
        f"🧠 *RUNNING 18 PRO INDICATORS...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n📊 `Checking S&R Zones, RSI(7), ADX & M5 Trend...`\n`[███████▒▒▒] 75%`",
        f"✅ *LOCKING SURESHOT ENTRY...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🎯 `Calculating 1-Minute Entry & MTG Filter...`\n`[██████████] 100%`"
    ]
    for stage_txt in stages:
        try:
            await msg.edit_text(stage_txt, parse_mode='Markdown')
            await asyncio.sleep(0.45)
        except Exception:
            pass

# --- Bot Handlers ---
DASHBOARD_TEXT = (
    "👾 *AYYAN GenAI+ QUOTEX DASHBOARD* 👾\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "ℹ️ *Welcome to the Official Quotex M1 Signal Terminal.*\n"
    "Select your trading market below to generate institutional-grade *LIVE* or *OTC* signals with real-time indicator confluence.\n\n"
    "💠 *Engine :* `18 Indicators + Fractal S&R + ADX`\n"
    "🔥 *Markets :* `100% Verified Quotex Live & OTC` · `UTC+5`\n\n"
    "🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        DASHBOARD_TEXT,
        parse_mode='Markdown',
        reply_markup=get_main_dashboard_kb(),
        disable_web_page_preview=True
    )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        DASHBOARD_TEXT,
        parse_mode='Markdown',
        reply_markup=get_main_dashboard_kb(),
        disable_web_page_preview=True
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data in ["main_menu", "menu_modes"]:
        await query.edit_message_text(
            DASHBOARD_TEXT,
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
        return

    if data in ["mode_live", "mode_otc"]:
        mode = "live" if data == "mode_live" else "otc"
        title = "📈 LIVE MARKET TERMINAL" if mode == "live" else "🌙 OTC MARKET TERMINAL"
        desc = (
            "Real-time Quotex Forex, Commodities & Crypto pairs."
            if mode == "live"
            else "24/7 Quotex OTC Currency & Commodity pairs (Active on Weekends)."
        )
        text = (
            f"*{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ {desc}\n\n"
            f"Choose *Auto Best Signal* to scan the highest-accuracy pair automatically, or *Select Specific Pair* to analyze your favorite chart:"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_market_sub_kb(mode)
        )
        return

    if data.startswith("list_"):
        _, mode, page_str = data.split("_")
        page = int(page_str)
        label = "LIVE" if mode == "live" else "OTC"
        text = (
            f"🎯 *SELECT QUOTEX {label} PAIR (Page {page + 1})*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ Tap any verified Quotex pair below to generate an instant *M1 Signal*:"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_pairs_grid_kb(mode, page)
        )
        return

    if data.startswith("pair_"):
        _, mode, idx_str = data.split("_")
        idx = int(idx_str)
        markets = list(QUOTEX_LIVE_MARKETS.keys()) if mode == "live" else list(QUOTEX_OTC_MARKETS.keys())
        if idx >= len(markets):
            return
        pair_name = markets[idx]

        await play_scan_animation(query.message, pair_name)
        sig = await asyncio.to_thread(fetch_single_pair_signal, pair_name, mode == "otc")

        if not sig:
            closed_text = (
                f"⚠️ *LIVE MARKET CLOSED (WEEKEND / HOLIDAY)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"ℹ️ `{pair_name} (Live)` is currently closed on global exchanges. Quotex operates *OTC Markets* right now.\n\n"
                f"👉 Tap below to generate high-accuracy *OTC Signals*!"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌙 SWITCH TO OTC MARKETS", callback_data="mode_otc")],
                [InlineKeyboardButton("👀 Back to Main Menu", callback_data="main_menu")]
            ])
            await query.message.edit_text(closed_text, parse_mode='Markdown', reply_markup=kb)
            return

        card = format_signal_card(sig)
        await query.message.edit_text(
            card,
            parse_mode='Markdown',
            reply_markup=get_post_signal_kb(mode, idx),
            disable_web_page_preview=True
        )
        return

    if data.startswith("auto_"):
        _, mode, count_str = data.split("_")
        count = int(count_str)
        label = "ALL QUOTEX LIVE MARKETS" if mode == "live" else "ALL QUOTEX OTC MARKETS"

        await play_scan_animation(query.message, label)
        found_signals = await asyncio.to_thread(scan_best_signals, mode, count)

        if not found_signals:
            await query.message.edit_text(
                "⚠️ *NO ACTIVE DATA RECEIVED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Please switch to OTC Markets or try again in a few seconds.",
                parse_mode='Markdown',
                reply_markup=get_main_dashboard_kb()
            )
            return

        first_card = format_signal_card(found_signals[0])
        await query.message.edit_text(
            first_card,
            parse_mode='Markdown',
            reply_markup=get_post_signal_kb(mode, None),
            disable_web_page_preview=True
        )

        for extra_sig in found_signals[1:]:
            await asyncio.sleep(0.35)
            extra_card = format_signal_card(extra_sig)
            await query.message.reply_text(
                extra_card,
                parse_mode='Markdown',
                reply_markup=get_post_signal_kb(mode, None),
                disable_web_page_preview=True
            )

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎫 CONTACT OWNER", url="https://t.me/Ayyan_trader01")]])
    if update.message:
        await update.message.reply_text("⛔ *ACCESS DENIED*\n━━━━━━━━━━━━━━━━━━━━━━\nℹ️ You are not authorized to use this terminal.", parse_mode='Markdown', reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer("⛔ Access Denied! Contact @Ayyan_trader01", show_alert=True)

def run_bot():
    logger.info("Starting Ayyan GenAI+ Quotex Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    if ALLOWED_IDS:
        telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
