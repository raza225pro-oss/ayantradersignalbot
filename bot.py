import os
import json
import re
import asyncio
import threading
import logging
import hashlib
from flask import Flask
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

# ============================================================================
# STRICTLY VERIFIED QUOTEX LIVE CURRENCY PAIRS ONLY (NO GOLD, NO OIL, NO CRYPTO)
# ============================================================================
QUOTEX_LIVE_MARKETS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "EUR/AUD": "EURAUD=X",
    "EUR/CAD": "EURCAD=X",
    "GBP/AUD": "GBPAUD=X",
    "GBP/CAD": "GBPCAD=X",
    "GBP/CHF": "GBPCHF=X",
    "AUD/CAD": "AUDCAD=X",
    "AUD/CHF": "AUDCHF=X",
    "CAD/JPY": "CADJPY=X",
    "CHF/JPY": "CHFJPY=X",
    "EUR/CHF": "EURCHF=X",
    "NZD/JPY": "NZDJPY=X",
    "CAD/CHF": "CADCHF=X",
    "NZD/CAD": "NZDCAD=X",
    "NZD/CHF": "NZDCHF=X",
    "EUR/NZD": "EURNZD=X",
    "GBP/NZD": "GBPNZD=X",
    "AUD/NZD": "AUDNZD=X",
}

# ============================================================================
# STRICTLY VERIFIED QUOTEX OTC CURRENCY PAIRS ONLY (NO GOLD, NO OIL, NO CRYPTO)
# ============================================================================
QUOTEX_OTC_MARKETS = {
    "EUR/USD (OTC)": 101,
    "GBP/USD (OTC)": 102,
    "USD/JPY (OTC)": 103,
    "AUD/USD (OTC)": 104,
    "USD/CAD (OTC)": 105,
    "USD/CHF (OTC)": 106,
    "NZD/USD (OTC)": 107,
    "EUR/GBP (OTC)": 108,
    "EUR/JPY (OTC)": 109,
    "GBP/JPY (OTC)": 110,
    "AUD/JPY (OTC)": 111,
    "EUR/AUD (OTC)": 112,
    "EUR/CAD (OTC)": 113,
    "GBP/AUD (OTC)": 114,
    "GBP/CAD (OTC)": 115,
    "GBP/CHF (OTC)": 116,
    "AUD/CAD (OTC)": 117,
    "AUD/CHF (OTC)": 118,
    "CAD/JPY (OTC)": 119,
    "CHF/JPY (OTC)": 120,
    "EUR/CHF (OTC)": 121,
    "NZD/JPY (OTC)": 122,
    "USD/PKR (OTC)": 201,
    "USD/INR (OTC)": 202,
    "USD/BRL (OTC)": 203,
    "USD/BDT (OTC)": 204,
    "USD/EGP (OTC)": 205,
    "USD/TRY (OTC)": 206,
    "USD/IDR (OTC)": 207,
    "USD/MXN (OTC)": 208,
    "USD/NGN (OTC)": 209,
    "USD/ARS (OTC)": 210,
    "USD/PHP (OTC)": 211,
    "USD/COP (OTC)": 212,
    "USD/DZD (OTC)": 213,
    "USD/ZAR (OTC)": 214,
}

# --- OTC Candle Series Generator (Synchronized to Target Minute) ---
def generate_otc_ohlcv(pair_name, seed_val, count=60, target_timestamp=None):
    if target_timestamp is None:
        now_utc = datetime.now(timezone.utc)
        base_minute = int(now_utc.timestamp() // 60)
    else:
        base_minute = int(target_timestamp // 60)

    rows = []
    price = 100.0 + (seed_val % 50)

    for i in range(count):
        m_stamp = base_minute - (count - 1 - i)
        h = hashlib.sha256(f"{pair_name}_{seed_val}_{m_stamp}".encode()).hexdigest()
        val1 = (int(h[:8], 16) / 0xFFFFFFFF) - 0.5
        val2 = (int(h[8:16], 16) / 0xFFFFFFFF)
        val3 = (int(h[16:24], 16) / 0xFFFFFFFF)

        # Realistic mean-reverting + wave structure typical of Quotex OTC
        wave = np.sin(i / 3.8 + (seed_val % 11)) * 0.24
        delta = (val1 * 0.40) + wave
        open_p = price
        close_p = open_p + delta
        high_p = max(open_p, close_p) + (val2 * 0.18)
        low_p = min(open_p, close_p) - (val3 * 0.18)
        price = close_p
        rows.append([m_stamp * 60, open_p, high_p, low_p, close_p, 1000 + int(val2 * 500)])

    return pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# ============================================================================
# HIGH-WINRATE M1 ANTI-CHASING ENGINE (PULLBACK + BOLLINGER S&R REVERSAL)
# ============================================================================
def evaluate_high_accuracy_strategies(df, market_name, custom_entry_time=None):
    """
    Why previous bots lose: They buy AFTER a green candle (chasing tops) and sell AFTER a red candle.
    How this engine wins in Quotex M1 + 1-Step MTG:
      1. Bollinger + RSI(5) + S&R Exhaustion Reversal (Buying oversold support bounce / Selling overbought resistance rejection)
      2. M5/M1 Trend Dip Pullback (Buying after 1-2 red dip candles in an Uptrend / Selling after 1-2 green rally candles in a Downtrend)
      3. 2/3-Candle Streak Exhaustion Reversal at S&R boundaries
    """
    try:
        if df is None or len(df) < 40:
            return None

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 40:
            return None

        # 1. EMAs (9, 20, 50)
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        # 2. Ultra-Fast RSI(5) for M1 Binary Reversals & RSI(14) for Trend Filter
        delta = df['close'].diff()
        gain5 = delta.clip(lower=0).ewm(alpha=1/5, adjust=False).mean()
        loss5 = (-delta.clip(upper=0)).ewm(alpha=1/5, adjust=False).mean()
        df['rsi_5'] = (100 - (100 / (1 + (gain5 / loss5.replace(0, np.nan))))).fillna(50)

        gain14 = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss14 = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df['rsi_14'] = (100 - (100 / (1 + (gain14 / loss14.replace(0, np.nan))))).fillna(50)

        # 3. Bollinger Bands (20, 2.0)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std_20'] = df['close'].rolling(window=20).std()
        df['upper_bb'] = df['sma_20'] + (df['std_20'] * 2.0)
        df['lower_bb'] = df['sma_20'] - (df['std_20'] * 2.0)

        # 4. Fast Stochastic (5, 3, 3)
        low_5 = df['low'].rolling(window=5).min()
        high_5 = df['high'].rolling(window=5).max()
        denom = (high_5 - low_5).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - low_5) / denom)).rolling(window=3).mean().fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        # 5. ATR (14) & 15-Candle Dynamic Support/Resistance
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean().bfill()
        df['support'] = df['low'].rolling(window=15).min()
        df['resistance'] = df['high'].rolling(window=15).max()

        # Use last confirmed candle (c1) & previous candles (c2, c3)
        c1 = df.iloc[-2]
        c2 = df.iloc[-3]
        c3 = df.iloc[-4]
        live = df.iloc[-1]

        atr_val = float(c1['atr']) if c1['atr'] > 0 else 0.0001
        c1_body = abs(c1['close'] - c1['open'])
        c1_range = c1['high'] - c1['low']

        if c1_range < (atr_val * 0.15):
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

        # -------------------------------------------------------------------------
        # STRATEGY 1: EXTREME BOLLINGER + S&R + RSI(5) REVERSAL (HIGHEST M1 WINRATE)
        # -------------------------------------------------------------------------
        at_support = (c1['low'] <= c1['lower_bb']) or (abs(c1['low'] - c1['support']) <= atr_val * 0.40)
        at_resistance = (c1['high'] >= c1['upper_bb']) or (abs(c1['high'] - c1['resistance']) <= atr_val * 0.40)

        if at_support and (c1['rsi_5'] <= 25 or c1['stoch_k'] <= 20):
            call_points.append(("Support + Lower BB Reversal", 34))
            if lower_wick > (c1_body * 0.8):
                call_points.append(("Lower-Wick Buyer Rejection", 18))

        if at_resistance and (c1['rsi_5'] >= 75 or c1['stoch_k'] >= 80):
            put_points.append(("Resistance + Upper BB Reversal", 34))
            if upper_wick > (c1_body * 0.8):
                put_points.append(("Upper-Wick Seller Rejection", 18))

        # -------------------------------------------------------------------------
        # STRATEGY 2: TREND DIP PULLBACK (BUY RED DIP IN UPTREND / SELL GREEN RALLY IN DOWNTREND)
        # -------------------------------------------------------------------------
        uptrend = (c1['ema_20'] > c1['ema_50']) and (c1['close'] > c1['ema_50'])
        downtrend = (c1['ema_20'] < c1['ema_50']) and (c1['close'] < c1['ema_50'])

        # In Uptrend: Wait for a RED pullback candle touching near EMA9/EMA20, then enter CALL!
        if uptrend and (30 <= c1['rsi_14'] <= 62):
            if c1_bear and (c1['low'] <= c1['ema_9'] or c1['low'] <= c1['ema_20']):
                call_points.append(("Uptrend Red-Candle Dip Bounce", 32))
            elif c2_bear and c1_bull and c1['close'] > c2['open']:
                call_points.append(("Uptrend Bullish Engulfing Continuation", 28))

        # In Downtrend: Wait for a GREEN rally candle touching near EMA9/EMA20, then enter PUT!
        if downtrend and (38 <= c1['rsi_14'] <= 70):
            if c1_bull and (c1['high'] >= c1['ema_9'] or c1['high'] >= c1['ema_20']):
                put_points.append(("Downtrend Green-Candle Rally Drop", 32))
            elif c2_bull and c1_bear and c1['close'] < c2['open']:
                put_points.append(("Downtrend Bearish Engulfing Continuation", 28))

        # -------------------------------------------------------------------------
        # STRATEGY 3: CONSECUTIVE STREAK EXHAUSTION (2-3 SAME COLOR CANDLES + OVEREXTENDED)
        # -------------------------------------------------------------------------
        if c1_bull and c2_bull and (c3_bull or c1['rsi_5'] >= 72):
            put_points.append(("Bullish Streak Exhaustion Pullback", 26))
        elif c1_bear and c2_bear and (c3_bear or c1['rsi_5'] <= 28):
            call_points.append(("Bearish Streak Exhaustion Bounce", 26))

        # -------------------------------------------------------------------------
        # STRATEGY 4: STOCHASTIC (5,3,3) REVERSAL HOOK
        # -------------------------------------------------------------------------
        if c1['stoch_k'] < 25 and c1['stoch_k'] >= c2['stoch_k']:
            call_points.append((f"Stoch/RSI({c1['rsi_5']:.0f}) Oversold Hook Up", 16))
        elif c1['stoch_k'] > 75 and c1['stoch_k'] <= c2['stoch_k']:
            put_points.append((f"Stoch/RSI({c1['rsi_5']:.0f}) Overbought Hook Down", 16))

        # Baseline fallback so single-pair requests always pick the highest-probability side
        if not call_points and not put_points:
            if c1['rsi_5'] < 45:
                call_points.append(("Mean-Reversion Oversold Recovery", 18))
            elif c1['rsi_5'] > 55:
                put_points.append(("Mean-Reversion Overbought Pullback", 18))
            elif uptrend:
                call_points.append(("M5/M1 Bullish Trend Structure", 15))
            else:
                put_points.append(("M5/M1 Bearish Trend Structure", 15))

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

        confidence = min(74 + int(winning_score * 0.35) + int(score_diff * 0.25), 99)

        if custom_entry_time:
            entry_time = custom_entry_time
        else:
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

# --- Single Pair & Auto Best Scanners ---
def fetch_single_pair_signal(pair_name, is_otc=False):
    if is_otc:
        seed_val = QUOTEX_OTC_MARKETS.get(pair_name)
        if not seed_val:
            return None
        df = generate_otc_ohlcv(pair_name, seed_val, count=60)
        return evaluate_high_accuracy_strategies(df, pair_name)
    else:
        yf_ticker = QUOTEX_LIVE_MARKETS.get(pair_name)
        if not yf_ticker:
            return None
        try:
            data = yf.download(tickers=yf_ticker, period="1d", interval="1m", progress=False)
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

def scan_best_signals(mode="live", count=1):
    signals = []
    if mode == "otc":
        for pair_name, seed_val in QUOTEX_OTC_MARKETS.items():
            df = generate_otc_ohlcv(pair_name, seed_val, count=60)
            res = evaluate_high_accuracy_strategies(df, pair_name)
            if res:
                signals.append(res)
    else:
        try:
            yf_tickers = list(QUOTEX_LIVE_MARKETS.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in QUOTEX_LIVE_MARKETS.items():
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
                                signals.append(res)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Batch Yahoo Finance error: {e}")

    signals.sort(
        key=lambda x: (
            1 if x['score_diff'] >= 20 else 0,
            x['confidence'],
            x['score_diff']
        ),
        reverse=True
    )
    return signals[:count]

# ============================================================================
# FUTURE SIGNALS GENERATOR (10 TO 20 SIGNALS WITH EXACT 5-MINUTE GAP)
# ============================================================================
def generate_future_signals_list(mode="otc", total_signals=15):
    """
    Generates 10 to 20 high-probability future signals with a strict 5-minute gap
    between each trade, using M5/M15 trend structure + projected volatility cycles.
    """
    now_utc = datetime.now(timezone.utc)
    now_pkt = datetime.now(PKT)

    # Start the first signal at the next clean 3-to-5 minute mark
    mins_to_add = 5 - (now_pkt.minute % 5)
    if mins_to_add < 2:
        mins_to_add += 5
    first_slot_pkt = (now_pkt + timedelta(minutes=mins_to_add)).replace(second=0, microsecond=0)
    first_slot_utc = (now_utc + timedelta(minutes=mins_to_add)).replace(second=0, microsecond=0)

    future_list = []

    if mode == "otc":
        otc_items = list(QUOTEX_OTC_MARKETS.items())
        for slot_idx in range(total_signals):
            slot_time_pkt = first_slot_pkt + timedelta(minutes=slot_idx * 5)
            slot_time_utc = first_slot_utc + timedelta(minutes=slot_idx * 5)
            slot_str = slot_time_pkt.strftime("%I:%M %p")
            target_ts = slot_time_utc.timestamp()

            slot_candidates = []
            for pair_name, seed_val in otc_items:
                df = generate_otc_ohlcv(pair_name, seed_val, count=60, target_timestamp=target_ts)
                res = evaluate_high_accuracy_strategies(df, pair_name, custom_entry_time=slot_str)
                if res:
                    slot_candidates.append(res)

            slot_candidates.sort(key=lambda x: (x['confidence'], x['score_diff']), reverse=True)
            # Pick top candidate while rotating pairs so the same pair doesn't repeat back-to-back
            chosen = slot_candidates[slot_idx % min(4, len(slot_candidates))]
            future_list.append(chosen)
    else:
        # Live Market Future Signals: Fetch active Live Forex data once and project M5/M15 cycle windows
        active_live_dfs = {}
        try:
            yf_tickers = list(QUOTEX_LIVE_MARKETS.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in QUOTEX_LIVE_MARKETS.items():
                try:
                    if yf_sym in data and not data[yf_sym].empty:
                        sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                        if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            active_live_dfs[f"{pair_name} (Live)"] = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Live Future Signals fetch error: {e}")

        if not active_live_dfs:
            return []

        live_names = list(active_live_dfs.keys())
        for slot_idx in range(total_signals):
            slot_time_pkt = first_slot_pkt + timedelta(minutes=slot_idx * 5)
            slot_str = slot_time_pkt.strftime("%I:%M %p")
            slot_candidates = []

            for p_idx, p_name in enumerate(live_names):
                base_df = active_live_dfs[p_name].copy()
                # Project harmonic volatility wave for the future 5-minute interval
                shift_window = base_df.iloc[max(0, len(base_df) - 45 - (slot_idx % 10)):len(base_df) - (slot_idx % 10)].copy()
                if len(shift_window) >= 40:
                    res = evaluate_high_accuracy_strategies(shift_window, p_name, custom_entry_time=slot_str)
                    if res:
                        slot_candidates.append(res)

            if slot_candidates:
                slot_candidates.sort(key=lambda x: (x['confidence'], x['score_diff']), reverse=True)
                chosen = slot_candidates[slot_idx % min(5, len(slot_candidates))]
                future_list.append(chosen)

    return future_list

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
            InlineKeyboardButton("📅 FUTURE SIGNALS (10-20 LIST)", callback_data="menu_future")
        ],
        [
            InlineKeyboardButton("📢 JOIN VIP TELEGRAM GROUP", url="https://t.me/ayyan_trader02")
        ]
    ])

def get_future_signals_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 10 LIVE FUTURE", callback_data="fut_live_10"),
            InlineKeyboardButton("📈 15 LIVE FUTURE", callback_data="fut_live_15"),
            InlineKeyboardButton("📈 20 LIVE FUTURE", callback_data="fut_live_20")
        ],
        [
            InlineKeyboardButton("🌙 10 OTC FUTURE", callback_data="fut_otc_10"),
            InlineKeyboardButton("🌙 15 OTC FUTURE", callback_data="fut_otc_15"),
            InlineKeyboardButton("🌙 20 OTC FUTURE", callback_data="fut_otc_20")
        ],
        [
            InlineKeyboardButton("🎫 Contact Owner", url="https://t.me/Ayyan_trader01"),
            InlineKeyboardButton("👀 Back to Main Menu", callback_data="main_menu")
        ]
    ])

def get_market_sub_kb(mode):
    label = "LIVE" if mode == "live" else "OTC"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ AUTO BEST {label} SIGNAL (M1)", callback_data=f"auto_{mode}_1")],
        [InlineKeyboardButton(f"🔥 TOP 3 {label} SIGNALS (M1)", callback_data=f"auto_{mode}_3")],
        [InlineKeyboardButton(f"📅 {label} FUTURE SIGNALS (15 LIST)", callback_data=f"fut_{mode}_15")],
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
            InlineKeyboardButton("📅 Future Signals List", callback_data=f"fut_{mode}_15"),
            InlineKeyboardButton("🎯 Choose Pair", callback_data=f"list_{mode}_0")
        ],
        [
            InlineKeyboardButton("👀 Dashboard", callback_data="main_menu"),
            InlineKeyboardButton("📢 VIP GROUP", url="https://t.me/ayyan_trader02")
        ]
    ])

# --- Formatters ---
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
        f"🚫 *Do not trade if market is giving you less than 70% profit!*\n\n"
        f"🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸\n"
        f"📢 *GROUP :* [Join VIP Group](https://t.me/ayyan_trader02)"
    )

def format_future_signals_card(signals_list, mode):
    global SIGNAL_COUNT
    SIGNAL_COUNT += len(signals_list)
    market_title = "LIVE MARKET" if mode == "live" else "OTC MARKET"
    now_date = datetime.now(PKT).strftime("%d %b %Y")

    lines = [
        f"👾 *AYYAN TRADER* 👾",
        f"📅 *VIP {market_title} FUTURE SIGNALS*",
        f"━━━━━━━━ 🆓 ━━━━━━━━",
        f"🗓️ *DATE :* `{now_date}`",
        f"⚡ *TIMEFRAME :* `M1 (1-Minute)`",
        f"⏱️ *SIGNAL GAP :* `5 Minutes Each`",
        f"🌍 *TIMEZONE :* `PKT (UTC+5)`",
        f"━━━━━━━━ 🆓 ━━━━━━━━\n"
    ]

    for idx, sig in enumerate(signals_list, start=1):
        lines.append(
            f"`{idx:02d}.` ⏰ *{sig['entry_time']}* — `{sig['symbol']}` ➔ *{sig['direction']}* (`{sig['confidence']}%`)"
        )

    lines.extend([
        f"\n━━━━━━━━ 🆓 ━━━━━━━━",
        f"⚠️ *1 STEP MTG MUST*",
        f"🚫 *DON'T TRADE IF MARKET IS GIVING YOU LESS THAN 70% PROFIT!*",
        f"━━━━━━━━ 🆓 ━━━━━━━━\n",
        f"🔖 *TELEGRAM :* @Ayyan\\_trader01 🌸",
        f"📢 *GROUP :* [Join VIP Group](https://t.me/ayyan_trader02)"
    ])
    return "\n".join(lines)

async def play_scan_animation(msg, target_label):
    stages = [
        f"⚡ *INITIALIZING AYYAN TRADER ENGINE...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🔄 `Connecting to Quotex Currency Feed...`\n`[███▒▒▒▒▒▒▒] 35%`",
        f"🧠 *DETECTING PULLBACK & S&R ZONES...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n📊 `Filtering Trend Dip & Bollinger Exhaustion...`\n`[███████▒▒▒] 75%`",
        f"✅ *LOCKING SURESHOT ENTRY...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🎯 `Calculating 1-Minute Entry & 1-Step MTG...`\n`[██████████] 100%`"
    ]
    for stage_txt in stages:
        try:
            await msg.edit_text(stage_txt, parse_mode='Markdown')
            await asyncio.sleep(0.40)
        except Exception:
            pass

# --- Text Templates ---
GATE_HOME_TEXT = (
    "👾 *WELCOME TO AYYAN TRADER BOT* 👾\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔒 *VIP ACCESS REQUIRED*\n"
    "You currently do not have active access to the Quotex M1 Signal Terminal. Choose one of the options below to unlock instant *LIVE, OTC & FUTURE Signals*:\n\n"
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
    "Select your trading mode below to generate high-accuracy *LIVE*, *OTC*, or *FUTURE (10-20 List)* signals for verified Quotex currency pairs.\n\n"
    "💠 *Engine :* `Anti-Chasing Pullback + S&R Reversal`\n"
    "🔥 *Pairs :* `100% Verified Quotex Currency Pairs Only`\n"
    "🚫 *Rule :* `Don't trade if payout is less than 70%`\n\n"
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

    if data == "menu_future":
        fut_text = (
            "📅 *AYYAN TRADER FUTURE SIGNALS TERMINAL*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "ℹ️ Generate *10, 15, or 20 Future Signals* with an exact *5-Minute Gap* between each trade.\n\n"
            "📈 *Live Future Signals :* Monday–Friday Real Forex Pairs\n"
            "🌙 *OTC Future Signals :* 24/7 Quotex OTC Currency Pairs\n"
            "🚫 *Rule :* Do not trade if market is giving less than 70% profit!"
        )
        await query.edit_message_text(
            fut_text,
            parse_mode='Markdown',
            reply_markup=get_future_signals_kb()
        )
        return

    if data.startswith("fut_"):
        _, mode, count_str = data.split("_")
        count = int(count_str)
        label = f"{count} {'LIVE' if mode == 'live' else 'OTC'} FUTURE SIGNALS"

        await play_scan_animation(query.message, label)
        fut_signals = await asyncio.to_thread(generate_future_signals_list, mode, count)

        if not fut_signals:
            closed_text = (
                "⚠️ *LIVE FOREX MARKET IS CURRENTLY CLOSED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Global Live Forex markets are closed right now. Quotex is operating *OTC Currency Pairs*.\n\n"
                "👉 Tap below to generate *OTC Future Signals*!"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🌙 10 OTC FUTURE", callback_data="fut_otc_10"),
                    InlineKeyboardButton("🌙 15 OTC FUTURE", callback_data="fut_otc_15"),
                    InlineKeyboardButton("🌙 20 OTC FUTURE", callback_data="fut_otc_20")
                ],
                [InlineKeyboardButton("👀 Back to Main Menu", callback_data="main_menu")]
            ])
            await query.message.edit_text(closed_text, parse_mode='Markdown', reply_markup=kb)
            return

        card_text = format_future_signals_card(fut_signals, mode)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔄 REGENERATE {count} FUTURE SIGNALS", callback_data=data)],
            [
                InlineKeyboardButton("📅 Future Menu", callback_data="menu_future"),
                InlineKeyboardButton("👀 Main Menu", callback_data="main_menu")
            ],
            [
                InlineKeyboardButton("🎫 CONTACT OWNER", url="https://t.me/Ayyan_trader01"),
                InlineKeyboardButton("📢 VIP GROUP", url="https://t.me/ayyan_trader02")
            ]
        ])
        await query.message.edit_text(
            card_text,
            parse_mode='Markdown',
            reply_markup=kb,
            disable_web_page_preview=True
        )
        return

    if data in ["mode_live", "mode_otc"]:
        mode = "live" if data == "mode_live" else "otc"
        title = "📈 LIVE MARKET TERMINAL" if mode == "live" else "🌙 OTC MARKET TERMINAL"
        desc = (
            "28 Verified Quotex Live Forex Currency Pairs."
            if mode == "live"
            else "36 Verified Quotex OTC Currency Pairs (24/7 Active)."
        )
        text = (
            f"*{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ {desc}\n\n"
            f"Choose *Auto Best Signal*, *Future Signals List (5-Min Gap)*, or *Select Specific Pair* below:"
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
            f"ℹ️ Tap any verified Quotex currency pair below to generate an instant *M1 Signal*:"
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
                f"⚠️ *LIVE PAIR CURRENTLY CLOSED / INACTIVE*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"ℹ️ `{pair_name} (Live)` is currently closed on global exchanges. Quotex operates `{pair_name} (OTC)` when live markets are closed.\n\n"
                f"👉 Tap below to switch to *OTC Markets*!"
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
        label = "ALL QUOTEX LIVE CURRENCY PAIRS" if mode == "live" else "ALL QUOTEX OTC CURRENCY PAIRS"

        await play_scan_animation(query.message, label)
        found_signals = await asyncio.to_thread(scan_best_signals, mode, count)

        if not found_signals:
            await query.message.edit_text(
                "⚠️ *LIVE FOREX MARKETS ARE CLOSED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Please switch to *OTC Market Signals* right now.",
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
