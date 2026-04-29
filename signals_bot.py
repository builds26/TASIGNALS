"""
HWMF Signal Bot — runs every 15 minutes via GitHub Actions.
Fetches Kraken OHLC data, runs confluence engine, posts Strong setups to Telegram.
"""
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ============ CONFIG ============
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
STATE_FILE = 'signal_state.json'
MIN_CONFLUENCE = 0.60  # 60% — Strong setups only

PAIRS = [
    {'pair': 'XBTUSD',  'symbol': 'BTC',  'name': 'Bitcoin'},
    {'pair': 'ETHUSD',  'symbol': 'ETH',  'name': 'Ethereum'},
    {'pair': 'SOLUSD',  'symbol': 'SOL',  'name': 'Solana'},
    {'pair': 'XRPUSD',  'symbol': 'XRP',  'name': 'XRP'},
    {'pair': 'ADAUSD',  'symbol': 'ADA',  'name': 'Cardano'},
    {'pair': 'DOGEUSD', 'symbol': 'DOGE', 'name': 'Dogecoin'},
    {'pair': 'AVAXUSD', 'symbol': 'AVAX', 'name': 'Avalanche'},
    {'pair': 'LINKUSD', 'symbol': 'LINK', 'name': 'Chainlink'},
    {'pair': 'DOTUSD',  'symbol': 'DOT',  'name': 'Polkadot'},
    {'pair': 'LTCUSD',  'symbol': 'LTC',  'name': 'Litecoin'}
]
# 1H equivalent: 5-minute candles
INTERVAL = 5

# ============ TA INDICATORS (mirror of your JS) ============
def ema(values, period):
    out = []
    k = 2 / (period + 1)
    prev = None
    for i, v in enumerate(values):
        if i == 0:
            prev = v
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out

def sma(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1:i + 1]) / period)
    return out

def rsi(closes, period=14):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    g = l = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0: g += d
        else: l -= d
    aG, aL = g / period, l / period
    out[period] = 100 - 100 / (1 + (100 if aL == 0 else aG / aL))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gn = d if d > 0 else 0
        ls = -d if d < 0 else 0
        aG = (aG * (period - 1) + gn) / period
        aL = (aL * (period - 1) + ls) / period
        out[i] = 100 - 100 / (1 + (100 if aL == 0 else aG / aL))
    return out

def macd(closes, f=12, s=26, sp=9):
    eF = ema(closes, f)
    eS = ema(closes, s)
    m = [eF[i] - eS[i] for i in range(len(closes))]
    sig = ema(m, sp)
    hist = [m[i] - sig[i] for i in range(len(closes))]
    return m, sig, hist

def atr(candles, period=14):
    tr = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c['high'] - c['low'])
        else:
            pc = candles[i - 1]['close']
            tr.append(max(c['high'] - c['low'], abs(c['high'] - pc), abs(c['low'] - pc)))
    out = [None] * len(candles)
    if len(candles) < period: return out
    out[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(candles)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out

def bollinger(closes, p=20, m=2):
    mid = sma(closes, p)
    upper, lower = [], []
    for i in range(len(closes)):
        if i < p - 1:
            upper.append(None); lower.append(None); continue
        s = sum((closes[j] - mid[i]) ** 2 for j in range(i - p + 1, i + 1))
        sd = (s / p) ** 0.5
        upper.append(mid[i] + m * sd)
        lower.append(mid[i] - m * sd)
    return upper, mid, lower

def find_levels(candles, lb=5, n=3):
    hi, lo = [], []
    for i in range(lb, len(candles) - lb):
        is_h = is_l = True
        for j in range(i - lb, i + lb + 1):
            if j == i: continue
            if candles[j]['high'] >= candles[i]['high']: is_h = False
            if candles[j]['low'] <= candles[i]['low']: is_l = False
        if is_h: hi.append(candles[i]['high'])
        if is_l: lo.append(candles[i]['low'])
    def dedupe(arr):
        s = sorted(arr, reverse=True)
        out = []
        for v in s:
            if not any(abs(o - v) / v < 0.005 for o in out):
                out.append(v)
        return out
    return dedupe(hi)[:n], dedupe(lo)[:n]

# ============ SIGNAL ENGINE ============
def generate_signal(candles):
    if not candles or len(candles) < 60:
        return None
    cl = [c['close'] for c in candles]
    last = len(cl) - 1
    price = cl[last]

    rA = rsi(cl, 14)
    m_line, s_line, hist = macd(cl)
    e20 = ema(cl, 20); e50 = ema(cl, 50); e200 = ema(cl, 200)
    aA = atr(candles, 14)
    bbU, bbM, bbL = bollinger(cl, 20, 2)
    res, sup = find_levels(candles, 5, 3)

    r = rA[last]; m = m_line[last]; s = s_line[last]; h = hist[last]; hP = hist[last - 1]
    E20 = e20[last]; E50 = e50[last]; E200 = e200[last]; a = aA[last]

    long_votes = short_votes = 0

    # Trend EMA stack
    if E20 > E50 > E200: long_votes += 1
    elif E20 < E50 < E200: short_votes += 1

    # Long-term bias
    if price > E200: long_votes += 1
    else: short_votes += 1

    # RSI
    if r < 30 or (30 <= r and r > 50): long_votes += 1
    elif r > 70 or r <= 50: short_votes += 1

    # MACD
    if m > s and h > hP: long_votes += 1
    elif m < s and h < hP: short_votes += 1

    # Bollinger
    if price < bbL[last] or price > bbM[last]: long_votes += 1
    elif price > bbU[last] or price <= bbM[last]: short_votes += 1

    total = 5
    direction = 'NEUTRAL'
    confluence = max(long_votes, short_votes) / total
    if long_votes >= 4:
        direction = 'LONG'
        confluence = long_votes / total
    elif short_votes >= 4:
        direction = 'SHORT'
        confluence = short_votes / total

    sl = tp1 = tp2 = rr = None
    if direction == 'LONG' and a:
        sl = price - 1.5 * a
        tp1 = price + 1.5 * a
        tp2 = price + 3 * a
        ns = next((l for l in sup if l < price and l > sl * 0.98), None)
        if ns: sl = ns * 0.998
        rr = (tp2 - price) / (price - sl)
    elif direction == 'SHORT' and a:
        sl = price + 1.5 * a
        tp1 = price - 1.5 * a
        tp2 = price - 3 * a
        nr = next((l for l in res if l > price and l < sl * 1.02), None)
        if nr: sl = nr * 1.002
        rr = (price - tp2) / (sl - price)

    return {
        'direction': direction,
        'confluence': confluence,
        'price': price,
        'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rr': rr,
        'rsi': r,
    }

# ============ KRAKEN ============
def fetch_klines(pair, interval):
    url = f'https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}'
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    if data.get('error'):
        raise Exception(f"Kraken error: {data['error']}")
    result = data['result']
    arr = None
    for k, v in result.items():
        if k != 'last' and isinstance(v, list):
            arr = v; break
    if not arr:
        raise Exception('No OHLC data')
    return [{
        'ts': c[0] * 1000,
        'open': float(c[1]),
        'high': float(c[2]),
        'low': float(c[3]),
        'close': float(c[4])
    } for c in arr]

# ============ TELEGRAM ============
def send_telegram(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def fmt_price(n):
    if n is None: return '—'
    if n >= 1000: return f'{n:,.2f}'
    if n >= 1: return f'{n:.4f}'
    return f'{n:.6f}'

def format_message(pair_info, sig):
    arrow = '🟢' if sig['direction'] == 'LONG' else '🔴'
    pct = round(sig['confluence'] * 100)
    msg = f"{arrow} <b>{sig['direction']}</b>  ·  <b>{pair_info['symbol']}/USD</b>\n"
    msg += f"<i>Confluence: {pct}%  ·  RSI: {sig['rsi']:.1f}</i>\n\n"
    msg += f"Entry:  <code>${fmt_price(sig['price'])}</code>\n"
    msg += f"SL:     <code>${fmt_price(sig['sl'])}</code>  ({((sig['sl']-sig['price'])/sig['price']*100):+.2f}%)\n"
    msg += f"TP1:    <code>${fmt_price(sig['tp1'])}</code>  ({((sig['tp1']-sig['price'])/sig['price']*100):+.2f}%)\n"
    msg += f"TP2:    <code>${fmt_price(sig['tp2'])}</code>  ({((sig['tp2']-sig['price'])/sig['price']*100):+.2f}%)\n"
    if sig['rr']:
        msg += f"R:R:    <code>1:{sig['rr']:.2f}</code>\n"
    msg += f"\n<i>HWMF Signal Desk · {datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
    return msg

# ============ STATE ============
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ============ MAIN ============
def main():
    state = load_state()
    new_state = dict(state)
    posted_count = 0
    errors = []

    print(f"Checking {len(PAIRS)} pairs at interval {INTERVAL}m...")

    for p in PAIRS:
        try:
            candles = fetch_klines(p['pair'], INTERVAL)
            sig = generate_signal(candles)
            if not sig:
                continue

            # Build a fingerprint so we don't repost the same signal
            fingerprint = f"{sig['direction']}_{round(sig['confluence']*100)}"
            last_seen = state.get(p['pair'])

            print(f"  {p['symbol']}: {sig['direction']} {round(sig['confluence']*100)}%")

            if (sig['direction'] in ('LONG', 'SHORT')
                    and sig['confluence'] >= MIN_CONFLUENCE
                    and last_seen != fingerprint):
                send_telegram(format_message(p, sig))
                new_state[p['pair']] = fingerprint
                posted_count += 1
                print(f"  → POSTED {p['symbol']}")
            elif sig['direction'] == 'NEUTRAL':
                # Reset state on neutral so a future signal will fire fresh
                new_state.pop(p['pair'], None)

        except Exception as e:
            errors.append(f"{p['symbol']}: {e}")
            print(f"  ERROR {p['symbol']}: {e}")

    save_state(new_state)
    print(f"\nDone. Posted {posted_count} signals. {len(errors)} errors.")
    if errors:
        for e in errors:
            print(f"  - {e}")

if __name__ == '__main__':
    main()
