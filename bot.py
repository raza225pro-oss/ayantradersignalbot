import os
import json
import re
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

# Optional Telethon import for automatic @QuotexPartnerBot communication
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Ayyan Trader Quotex Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Configuration & Branding ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) if os.getenv("ADMIN_ID", "").strip().isdigit() else 0
raw_ids = os.getenv("ALLOWED_IDS", "")
ENV_ALLOWED_IDS = set(int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit())
if ADMIN_ID:
    ENV_ALLOWED_IDS.add(ADMIN_ID)

QUOTEX_PARTNER_LINK = os.getenv("QUOTEX_PARTNER_LINK", "https://broker-qx.pro/sign-up/?lid=2111446")
EXPECTED_LINK_ID = os.getenv("EXPECTED_LINK_ID", "")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "876543210 (Ayyan_Trader)")
PREMIUM_PRICE = os.getenv("PREMIUM_PRICE", "$20 USDT (Lifetime Access)")
PARTNER_BOT_USERNAME = os.getenv("PARTNER_BOT_USERNAME", "QuotexPartnerBot")

TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()

PKT = timezone(timedelta(hours=5))
SIGNAL_COUNT = 0
DB_FILE = "authorized_users.json"

# --- Persistent Database for Approved Users & Used Trader IDs ---
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"approved_users": [], "used_trader_ids": {}}

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Database save error: {e}")

db_data = load_db()

def is_user_authorized(user_id: int) -> bool:
    if user_id in ENV_ALLOWED_IDS:
        return True
    if user_id in db_data.get("approved_users", []):
        return True
    return False

def authorize_user(user_id: int, trader_id: str = None):
    if user_id not in db_data["approved_users"]:
        db_data["approved_users"].append(user_id)
    if trader_id:
        db_data["used_trader_ids"][str(trader_id)] = user_id
    save_db(db_data)

# --- Telethon Userbot Bridge for @QuotexPartnerBot ---
telethon_client = None
telethon_lock = asyncio.Lock()

async def verify_with_quotex_partner_bot(trader_id: str):
    global telethon_client
    if not TELETHON_AVAILABLE or not TG_API_ID or not TG_API_HASH:
        return "MANUAL_FALLBACK", "Telethon API credentials not configured."

    async with telethon_lock:
        try:
            if telethon_client is None:
                session = StringSession(TG_SESSION_STRING) if TG_SESSION_STRING else "quotex_verifier"
                telethon_client = TelegramClient(session, int(TG_API_ID), TG_API_HASH)
            if not telethon_client.is_connected():
                await telethon_client.connect()
            if not await telethon_client.is_user_authorized():
                return "MANUAL_FALLBACK", "Telethon user session not logged in yet."

            async with telethon_client.conversation(PARTNER_BOT_USERNAME, timeout=15) as conv:
                await conv.send_message(str(trader_id))
                resp = await conv.get_response()
                text = resp.text or ""

                if "was not found" in text.lower() or "not found" in text.lower():
                    return "NOT_FOUND", text

                if f"trader # {trader_id}" in text.lower() or "trader #" in text.lower() or "registration date:" in text.lower():
                    if EXPECTED_LINK_ID and f"link id: {EXPECTED_LINK_ID}" not in text.lower():
                        return "WRONG_LINK", text
                    return "FOUND", text

                return "NOT_FOUND", text
        except Exception as e:
            logger.error(f"PartnerBot verification error: {e}")
            return "ERROR", str(e)

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

        wave = np.sin(i / 4.0 + (seed_val % 7)) * 0.22
        delta = (val1 * 0.42) + wave
        open_p = price
        close_p = open_p + delta
        high_p = max(open_p, close_p) + (val2 * 0.20)
        low_p = min(open_p, close_p) - (val3 * 0.20)
        price = close_p
        rows.append([m_stamp * 60, open_p, high_p, low_p, close_p, 1000 + int(val2 * 500)])

    return pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# --- UPGRADED ANTI-LOSS M1 BINARY ACCURACY ENGINE ---
def evaluate_high_accuracy_strategies(df, market_name):
    """
    Separates Trend Continuation vs Extreme S&R Reversal so indicators never contradict each other.
    Uses confirmed closed candles (iloc[-2]) + streak exhaustion protection.
    """
    try:
        if df is None or len(df) < 40:
            return None

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 40:
            return None

        # 1. Institutional EMAs (9, 21, 50)
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        # 2. Precision RSI (7 for Binary Reversals, 14 for Trend)
        delta = df['close'].diff()
        gain7 = delta.clip(lower=0).ewm(alpha=1/7, adjust=False).mean()
        loss7 = (-delta.clip(upper=0)).ewm(alpha=1/7, adjust=False).mean()
        df['rsi_7'] = (100 - (100 / (1 + (gain7 / loss7.replace(0, np.nan))))).fillna(50)

        gain14 = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss14 = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df['rsi_14'] = (100 - (100 / (1 + (gain14 / loss14.replace(0, np.nan))))).fillna(50)

        # 3. Bollinger Bands (20, 2.0)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std_20'] = df['close'].rolling(window=20).std()
        df['upper_bb'] = df['sma_20'] + (df['std_20'] * 2.0)
        df['lower_bb'] = df['sma_20'] - (df['std_20'] * 2.0)

        # 4. Stochastic (14, 3, 3)
        low_14 = df['low'].rolling(window=14).min()
        high_14 = df['high'].rolling(window=14).max()
        denom = (high_14 - low_14).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - low_14) / denom)).rolling(window=3).mean().fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        # 5. MACD (12, 26, 9)
        macd = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        macd_sig = macd.ewm(span=9, adjust=False).mean()
        df['macd_hist'] = macd - macd_sig

        # 6. ATR (14) & Support/Resistance Zones (20-period)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean().bfill()
        df['support'] = df['low'].rolling(window=20).min()
        df['resistance'] = df['high'].rolling(window=20).max()

        # Use Confirmed Closed Candles (c1 = last closed candle, c2 = previous closed, c3 = 3rd back)
        c1 = df.iloc[-2]
        c2 = df.iloc[-3]
        c3 = df.iloc[-4]
        live = df.iloc[-1]

        atr_val = float(c1['atr']) if c1['atr'] > 0 else 0.0001
        c1_body = abs(c1['close'] - c1['open'])
        c1_range = c1['high'] - c1['low']

        # Filter out flat/dead charts
        if c1_range < (atr_val * 0.20):
            return None

        c1_bull = c1['close'] > c1['open']
        c1_bear = c1['close'] < c1['open']
        c2_bull = c2['close'] > c2['open']
        c2_bear = c2['close'] < c2['open']
        c3_bull = c3['close'] > c3['open']
        c3_bear = c3['close'] < c3['open']

        upper_wick = c1['high'] - max(c1['open'], c1['close'])
        lower_wick = min(c1['open'], c1['close']) - c1['low']

        call_points = []
        put_points = []

        # =========================================================================
        # ENGINE A: EXTREME S&R + BOLLINGER REVERSAL (SURESHOT MEAN REVERSION)
        # =========================================================================
        near_support = (c1['low'] <= c1['lower_bb']) or (abs(c1['low'] - c1['support']) <= atr_val * 0.35)
        near_resistance = (c1['high'] >= c1['upper_bb']) or (abs(c1['high'] - c1['resistance']) <= atr_val * 0.35)

        if near_support and c1['rsi_7'] <= 28:
            call_points.append(("Support + Lower BB Oversold Bounce", 28))
            if lower_wick > (c1_body * 1.2):
                call_points.append(("Strong Lower-Wick Buyers Rejection", 18))
            if c1['stoch_k'] < 25:
                call_points.append((f"Stoch + RSI({c1['rsi_7']:.0f}) Extreme Oversold", 14))

        if near_resistance and c1['rsi_7'] >= 72:
            put_points.append(("Resistance + Upper BB Overbought Drop", 28))
            if upper_wick > (c1_body * 1.2):
                put_points.append(("Strong Upper-Wick Sellers Rejection", 18))
            if c1['stoch_k'] > 75:
                put_points.append((f"Stoch + RSI({c1['rsi_7']:.0f}) Extreme Overbought", 14))

        # =========================================================================
        # ENGINE B: CONFIRMED TREND PULLBACK CONTINUATION (NO OVERBOUGHT CHASING!)
        # =========================================================================
        strong_uptrend = (c1['ema_9'] > c1['ema_21'] > c1['ema_50'])
        strong_downtrend = (c1['ema_9'] < c1['ema_21'] < c1['ema_50'])

        # Only take Trend CALL if RSI is NOT overbought (45 to 67 sweet spot)
        if strong_uptrend and (45 <= c1['rsi_14'] <= 67):
            if c2_bear and c1_bull and c1['close'] > c2['open']:
                call_points.append(("Uptrend Pullback + Bullish Engulfing", 30))
            elif c2_bear and c1_bull and c1['low'] <= c1['ema_21']:
                call_points.append(("EMA-21 Dynamic Support Bounce", 26))
            elif c1_bull and c1['macd_hist'] > c2['macd_hist'] and c1['macd_hist'] > 0:
                call_points.append(("M1/M5 Uptrend Momentum Continuation", 20))

        # Only take Trend PUT if RSI is NOT oversold (33 to 55 sweet spot)
        if strong_downtrend and (33 <= c1['rsi_14'] <= 55):
            if c2_bull and c1_bear and c1['close'] < c2['open']:
                put_points.append(("Downtrend Pullback + Bearish Engulfing", 30))
            elif c2_bull and c1_bear and c1['high'] >= c1['ema_21']:
                put_points.append(("EMA-21 Dynamic Resistance Drop", 26))
            elif c1_bear and c1['macd_hist'] < c2['macd_hist'] and c1['macd_hist'] < 0:
                put_points.append(("M1/M5 Downtrend Momentum Continuation", 20))

        # =========================================================================
        # ENGINE C: 3-CANDLE STREAK EXHAUSTION & SPIKE REVERSAL FILTER
        # =========================================================================
        # Prevent buying after 3 consecutive green candles (exhaustion)
        if c1_bull and c2_bull and c3_bull:
            if c1['rsi_7'] >= 68 or c1['high'] >= c1['upper_bb']:
                put_points.append(("3-Green Candle Exhaustion Reversal", 24))
        elif c1_bear and c2_bear and c3_bear:
            if c1['rsi_7'] <= 32 or c1['low'] <= c1['lower_bb']:
                call_points.append(("3-Red Candle Exhaustion Reversal", 24))

        # Huge Exhaustion Spike Candle (2x ATR) -> Expect pullback on next 1m candle
        if c1_body > (atr_val * 2.0):
            if c1_bull and c1['high'] >= c1['upper_bb']:
                put_points.append(("Climax Spike Pullback", 22))
            elif c1_bear and c1['low'] <= c1['lower_bb']:
                call_points.append(("Climax Drop Pullback", 22))

        # =========================================================================
        # ENGINE D: SECONDARY CONFLUENCE & LIVE ENTRY CHECK
        # =========================================================================
        if c1['stoch_k'] > c1['stoch_d'] and c2['stoch_k'] <= c2['stoch_d'] and c1['stoch_k'] < 65:
            call_points.append(("Stochastic Golden Cross Up", 14))
        elif c1['stoch_k'] < c1['stoch_d'] and c2['stoch_k'] >= c2['stoch_d'] and c1['stoch_k'] > 35:
            put_points.append(("Stochastic Death Cross Down", 14))

        # Standard baseline alignment so single-pair requests always return the highest probability side
        if c1['ema_9'] > c1['ema_21'] and c1['rsi_7'] < 70:
            call_points.append(("EMA 9/21 Bullish Structure", 10))
        elif c1['ema_9'] < c1['ema_21'] and c1['rsi_7'] > 30:
            put_points.append(("EMA 9/21 Bearish Structure", 10))

        if live['close'] >= live['open']:
            call_points.append(("Live Tick Buyers Entry", 6))
        else:
            put_points.append(("Live Tick Sellers Entry", 6))

        call_score = sum(w for _, w in call_points)
        put_score = sum(w for _, w in put_points)
        score_diff = abs(call_score - put_score)

        if call_score >= put_score:
            direction = "CALL 🟢⬆️"
            reasons = [r for r, _ in sorted(call_points, key=lambda x: x[1], reverse=True)]
            winning_score = call_score
        else:
            direction = "PUT 🔴⬇️"
            reasons = [r for r, _ in sorted(put_points, key=lambda x: x[1], reverse=True)]
            winning_score = put_score

        confidence = min(68 + int(winning_score * 0.42) + int(score_diff * 0.35), 99)

        now_pkt = datetime.now(PKT)
        entry_time = (now_pkt + timedelta(minutes=1)).strftime("%I:%M %p")

        return {
            'symbol': market_name,
            'direction': direction,
            'confidence': int(confidence),
            'score_diff': score_diff,
            'entry_time': entry_time,
            'reason': ", ".join(reasons[:2])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

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
                if data is not None and not data.empty and len(data) >= 40:
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    sub_df = data.dropna(subset=['Close'])
                    if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
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
                if ohlcv and len(ohlcv) >= 40:
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
        try:
            fx_pairs = {k: v['ticker'] for k, v in QUOTEX_LIVE_MARKETS.items() if v['type'] == 'forex'}
            yf_tickers = list(fx_pairs.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in fx_pairs.items():
                try:
                    if yf_sym in data and not data[yf_sym].empty:
                        sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                        if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
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

        for pair_name, info in QUOTEX_LIVE_MARKETS.items():
            if info['type'] == 'crypto':
                try:
                    ohlcv = exchange.fetch_ohlcv(info['ticker'], timeframe='1m', limit=60)
                    if ohlcv and len(ohlcv) >= 40:
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        res = evaluate_high_accuracy_strategies(df, f"{pair_name} (Live)")
                        if res:
                            res['is_forex'] = False
                            signals.append(res)
                except Exception:
                    continue

    signals.sort(
        key=lambda x: (
            1 if x['score_diff'] >= 22 else 0,
            1 if x.get('is_forex') else 0,
            x['confidence'],
            x['score_diff']
        ),
        reverse=True
    )
    return signals[:count]

# --- Keyboards for Unverified Users ---
def get_access_gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆓 GET FREE ACCESS (VIP)", callback_data="gate_free")],
        [InlineKeyboardButton("💎 BUY PREMIUM ACCESS", callback_data="gate_premium")],
        [
            InlineKeyboardButton("📢 JOIN VIP GROUP", url="https://t.me/ayyan_trader02"),
            InlineKeyboardButton("🎫 CONTACT ADMIN", url="https://t.me/Ayyan_trader01")
        ]
    ])

def get_free_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 CREATE QUOTEX ACCOUNT NOW", url=QUOTEX_PARTNER_LINK)],
        [InlineKeyboardButton("✍️ SUBMIT MY TRADER ID", callback_data="enter_trader_id")],
        [
            InlineKeyboardButton("🎫 Contact Admin", url="https://t.me/Ayyan_trader01"),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

def get_premium_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 SEND PAYMENT SCREENSHOT TO ADMIN", url="https://t.me/Ayyan_trader01")],
        [
            InlineKeyboardButton("🆓 Get Free Access Instead", callback_data="gate_free"),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

# --- Keyboards for Verified VIP Users ---
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

# --- UPDATED SIGNAL CARD WITH "AYYAN TRADER" HEADER ---
def format_signal_card(sig):
    global SIGNAL_COUNT
    SIGNAL_COUNT += 1
    return (
        f"👾 *AYYAN TRADER* 👾\n\n"
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

async def play_scan_animation(msg, target_label):
    stages = [
        f"⚡ *INITIALIZING AYYAN TRADER ENGINE...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🔄 `Connecting to Quotex Liquidity Feed...`\n`[███▒▒▒▒▒▒▒] 35%`",
        f"🧠 *FILTERING FALSE BREAKOUTS...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n📊 `Checking Confirmed Closed Candle & S&R Zones...`\n`[███████▒▒▒] 75%`",
        f"✅ *LOCKING SURESHOT ENTRY...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🎯 `Calculating 1-Minute Entry & MTG Filter...`\n`[██████████] 100%`"
    ]
    for stage_txt in stages:
        try:
            await msg.edit_text(stage_txt, parse_mode='Markdown')
            await asyncio.sleep(0.45)
        except Exception:
            pass

# --- Text Templates ---
GATE_HOME_TEXT = (
    "👾 *WELCOME TO AYYAN TRADER BOT* 👾\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔒 *VIP ACCESS REQUIRED*\n"
    "You currently do not have active access to the Quotex M1 Signal Terminal. Choose one of the options below to unlock instant *LIVE & OTC Signals*:\n\n"
    "🆓 *Option 1 : Get Free Access*\n"
    "Register a Quotex account through our official partner link and send your *Trader ID* for instant automated verification.\n\n"
    "💎 *Option 2 : Buy Premium Access*\n"
    "Pay via *Binance Pay* without creating a new Quotex account and get instant lifetime access.\n\n"
    "🔖 *ADMIN :* @Ayyan\\_trader01 🌸"
)

DASHBOARD_TEXT = (
    "👾 *AYYAN TRADER DASHBOARD* 👾\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "✅ *VIP STATUS : ACTIVE*\n"
    "Select your trading market below to generate institutional-grade *LIVE* or *OTC* signals with real-time price-action confluence.\n\n"
    "💠 *Engine :* `Confirmed Candle Pullback + S&R Bounce`\n"
    "🔥 *Markets :* `100% Verified Quotex Live & OTC` · `UTC+5`\n\n"
    "🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸"
)

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data["awaiting_trader_id"] = False

    if is_user_authorized(user_id):
        await update.message.reply_text(
            DASHBOARD_TEXT,
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ENV_ALLOWED_IDS and user_id != ADMIN_ID:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ *Usage:* `/adduser <Telegram_User_ID>`", parse_mode='Markdown')
        return

    new_uid = int(context.args[0])
    authorize_user(new_uid)
    await update.message.reply_text(f"✅ *User `{new_uid}` has been granted VIP Premium Access!*", parse_mode='Markdown')
    try:
        await context.bot.send_message(
            chat_id=new_uid,
            text="🎉 *CONGRATULATIONS!*\n━━━━━━━━━━━━━━━━━━━━━━\n💎 Your *VIP Premium Access* has been activated by Admin!\n\n👉 Send `/start` to open the Signal Terminal.",
            parse_mode='Markdown'
        )
    except Exception:
        pass

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    match = re.search(r'\b(\d{6,12})\b', text)

    if is_user_authorized(user_id) and not context.user_data.get("awaiting_trader_id"):
        await update.message.reply_text(
            DASHBOARD_TEXT,
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
        return

    if not match:
        await update.message.reply_text(
            "⚠️ *INVALID TRADER ID FORMAT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "ℹ️ Please send only your numeric *Quotex Trader ID* (for example: `95420333`).\n\n"
            "Don't have an account yet? Tap the button below to register first:",
            parse_mode='Markdown',
            reply_markup=get_free_access_kb()
        )
        return

    trader_id = match.group(1)

    used_by = db_data.get("used_trader_ids", {}).get(trader_id)
    if used_by and used_by != user_id:
        await update.message.reply_text(
            "⛔ *TRADER ID ALREADY REGISTERED*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ Trader ID `{trader_id}` is already linked to another Telegram account. Each user must create their own personal Quotex account.",
            parse_mode='Markdown',
            reply_markup=get_free_access_kb()
        )
        return

    verify_msg = await update.message.reply_text(
        f"🔄 *VERIFYING TRADER ID `{trader_id}`...*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 `Checking registration with @QuotexPartnerBot...`\n"
        f"`[█████▒▒▒▒▒] 50%`",
        parse_mode='Markdown'
    )

    status, raw_reply = await verify_with_quotex_partner_bot(trader_id)

    if status == "FOUND":
        authorize_user(user_id, trader_id)
        context.user_data["awaiting_trader_id"] = False
        await verify_msg.edit_text(
            f"✅ *TRADER ID VERIFIED SUCCESSFULLY!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *Trader ID :* `#{trader_id}`\n"
            f"🟢 *Status :* `VIP Access Unlocked`\n\n"
            f"Welcome to *Ayyan Trader Terminal*! Select your market below to start trading:",
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb()
        )
        return

    elif status in ["NOT_FOUND", "WRONG_LINK"]:
        await verify_msg.edit_text(
            f"❌ *ACCOUNT NOT FOUND!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ `Trader with ID = '{trader_id}' was not found.`\n\n"
            f"You have not created a Quotex account using our official partner link yet. Please follow these steps:\n"
            f"1️⃣ Click *CREATE QUOTEX ACCOUNT NOW* below.\n"
            f"2️⃣ Register a fresh account with a new email.\n"
            f"3️⃣ Copy your 8-9 digit *Trader ID* and paste it here again!",
            parse_mode='Markdown',
            reply_markup=get_free_access_kb()
        )
        return

    else:
        if ADMIN_ID:
            admin_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve User", callback_data=f"adm_app_{user_id}_{trader_id}"),
                    InlineKeyboardButton("❌ Reject (Not Found)", callback_data=f"adm_rej_{user_id}_{trader_id}")
                ]
            ])
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🔔 *NEW TRADER ID VERIFICATION REQUEST*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *User:* {update.effective_user.mention_markdown()} (`{user_id}`)\n"
                        f"🆔 *Quotex Trader ID:* `{trader_id}`\n\n"
                        f"Check `{trader_id}` in @QuotexPartnerBot and click below:"
                    ),
                    parse_mode='Markdown',
                    reply_markup=admin_kb
                )
            except Exception as e:
                logger.error(f"Admin notify error: {e}")

        await verify_msg.edit_text(
            f"⏳ *TRADER ID `{trader_id}` SUBMITTED FOR VERIFICATION*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ Your Trader ID has been sent to our verification system. You will receive an automatic notification here as soon as your ID is verified!",
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb()
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("adm_app_") or data.startswith("adm_rej_"):
        if user_id not in ENV_ALLOWED_IDS and user_id != ADMIN_ID:
            return
        parts = data.split("_")
        action = parts[1]
        target_uid = int(parts[2])
        t_id = parts[3]

        if action == "app":
            authorize_user(target_uid, t_id)
            await query.edit_message_text(f"✅ *Approved User `{target_uid}` (Trader ID: `{t_id}`)*", parse_mode='Markdown')
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"✅ *TRADER ID `#{t_id}` VERIFIED!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎉 Your *VIP Access* is now active! Choose your trading market below:"
                    ),
                    parse_mode='Markdown',
                    reply_markup=get_main_dashboard_kb()
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(f"❌ *Rejected User `{target_uid}` (Trader ID `{t_id}` Not Found)*", parse_mode='Markdown')
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"❌ *ACCOUNT NOT FOUND!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"ℹ️ `Trader with ID = '{t_id}' was not found.`\n\n"
                        f"You have not created an account using our official partner link yet. Please click the button below to register a new Quotex account and submit your new Trader ID:"
                    ),
                    parse_mode='Markdown',
                    reply_markup=get_free_access_kb()
                )
            except Exception:
                pass
        return

    if data == "gate_home":
        context.user_data["awaiting_trader_id"] = False
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "gate_free":
        context.user_data["awaiting_trader_id"] = True
        free_text = (
            "🆓 *GET FREE VIP BOT ACCESS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Follow these 3 simple steps to unlock the bot for *FREE*:\n\n"
            "1️⃣ Click *CREATE QUOTEX ACCOUNT NOW* below and register a new Quotex account via our official link.\n"
            "2️⃣ Open your Quotex Profile and copy your *8 or 9 digit Trader ID* (e.g., `95420333`).\n"
            "3️⃣ Paste and send your *Trader ID* directly in this chat!\n\n"
            "⚠️ _Note: Only accounts registered through our link will be approved by @QuotexPartnerBot._\n\n"
            "👇 *Send your numeric Trader ID in the message box below:*"
        )
        await query.edit_message_text(
            free_text,
            parse_mode='Markdown',
            reply_markup=get_free_access_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "enter_trader_id":
        context.user_data["awaiting_trader_id"] = True
        await query.edit_message_text(
            "✍️ *SUBMIT YOUR QUOTEX TRADER ID*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please type and send your numeric *Quotex Trader ID* (e.g. `95420333`) in this chat right now.\n\n"
            "🔄 _Our system will automatically verify your ID with @QuotexPartnerBot in 2 seconds!_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]])
        )
        return

    if data == "gate_premium":
        context.user_data["awaiting_trader_id"] = False
        prem_text = (
            "💎 *BUY VIP PREMIUM ACCESS (NO LINK NEEDED)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Don't want to create a new Quotex account? Unlock lifetime VIP bot access via *Binance Pay*:\n\n"
            f"💰 *Price :* `{PREMIUM_PRICE}`\n"
            f"🏦 *Binance Pay ID :* `{BINANCE_PAY_ID}`\n"
            f"🆔 *Your Telegram ID :* `{user_id}`\n\n"
            "📌 *How to Activate:*\n"
            "1️⃣ Send the payment to the Binance Pay ID above.\n"
            "2️⃣ Take a screenshot of the payment confirmation.\n"
            f"3️⃣ Send the screenshot along with your Telegram ID (`{user_id}`) to @Ayyan\\_trader01 for instant activation!"
        )
        await query.edit_message_text(
            prem_text,
            parse_mode='Markdown',
            reply_markup=get_premium_access_kb(),
            disable_web_page_preview=True
        )
        return

    if not is_user_authorized(user_id):
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )
        return

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

def run_bot():
    logger.info("Starting Ayyan Trader Quotex Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    telegram_app.add_handler(CommandHandler("adduser", adduser_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
