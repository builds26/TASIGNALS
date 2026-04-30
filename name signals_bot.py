"""HWMF Snapshot Bot — leverage edition."""
import os, json, urllib.request, urllib.parse
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

PAIRS = [
    {'pair': 'XBTUSD',  'symbol': 'BTC'},
    {'pair': 'ETHUSD',  'symbol': 'ETH'},
    {'pair': 'SOLUSD',  'symbol': 'SOL'},
    {'pair': 'XRPUSD',  'symbol': 'XRP'},
    {'pair': 'ADAUSD',  'symbol': 'ADA'},
    {'pair': 'DOGEUSD', 'symbol': 'DOGE'},
    {'pair': 'AVAXUSD', 'symbol': 'AVAX'},
    {'pair': 'LINKUSD', 'symbol': 'LINK'},
    {'pair': 'DOTUSD',  'symbol': 'DOT'},
    {'pair': 'LTCUSD',  'symbol': 'LTC'}
]
INTERVAL = 15  # 15-minute candles

def ema(values, period):
    out = []; k = 2/(period+1); prev = None
    for i, v in enumerate(values):
        prev = v if i == 0 else v*k + prev*(1-k)
        out.append(prev)
    return out

def sma(values, period):
    out = []
    for i in range(len(values)):
        out.append(None if i < period-1 else sum(values[i-period+1:i+1])/period)
    return out

def rsi(closes, period=14):
    out = [None]*len(closes)
    if len(closes) < period+1: return out
    g = l = 0
    for i in range(1, period+1):
        d = closes[i]-closes[i-1]
        if d >= 0: g += d
        else: l -= d
    aG, aL = g/period, l/period
    out[period] = 100 - 100/(1+(100 if aL == 0 else aG/aL))
    for i in range(period+1, len(closes)):
        d = closes[i]-closes[i-1]
        gn = d if d > 0 else 0
        ls = -d if d < 0 else 0
        aG = (aG*(period-1)+gn)/period
        aL = (aL*(period-1)+ls)/period
        out[i] = 100 - 100/(1+(100 if aL == 0 else aG/aL))
    return out

def macd(closes, f=12, s=26, sp=9):
    eF = ema(closes, f); eS = ema(closes, s)
    m = [eF[i]-eS[i] for i in range(len(closes))]
    sig = ema(m, sp)
    hist = [m[i]-sig[i] for i in range(len(closes))]
    return m, sig, hist

def atr(candles, period=14):
    tr = []
    for i, c in enumerate(candles):
        if i == 0: tr.append(c['high']-c['low'])
        else:
            pc = candles[i-1]['close']
            tr.append(max(c['high']-c['low'], abs(c['high']-pc), abs(c['low']-pc)))
    out = [None]*len(candles)
    if len(candles) < period: return out
    out[period-1] = sum(tr[:period])/period
    for i in range(period, len(candles)):
        out[i] = (out[i-1]*(period-1)+tr[i])/period
    return out

def bollinger(closes, p=20, m=2):
    mid = sma(closes, p); upper = []; lower = []
    for i in range(len(closes)):
        if i < p-1:
            upper.append(None); lower.append(None); continue
        s = sum((closes[j]-mid[i])**2 for j in range(i-p+1, i+1))
        sd = (s/p)**0.5
        upper.append(mid[i]+m*sd); lower.append(mid[i]-m*sd)
    return upper, mid, lower

def generate_signal(candles):
    if not candles or len(candles) < 60: return None
    cl = [c['close'] for c in candles]
    last = len(cl)-1; price = cl[last]
    rA = rsi(cl, 14); m_line, s_line, hist = macd(cl)
    e20 = ema(cl, 20); e50 = ema(cl, 50); e200 = ema(cl, 200)
    aA = atr(candles, 14); bbU, bbM, bbL = bollinger(cl, 20, 2)
    r = rA[last]; m = m_line[last]; s = s_line[last]
    h = hist[last]; hP = hist[last-1]
    E20 = e20[last]; E50 = e50[last]; E200 = e200[last]; a = aA[last]
    long_v = short_v = 0
    if E20 > E50 > E200: long_v += 1
    elif E20 < E50 < E200: short_v += 1
    if price > E200: long_v += 1
    else: short_v += 1
    if r < 30 or r > 50: long_v += 1
    elif r > 70 or r <= 50: short_v += 1
    if m > s and h > hP: long_v += 1
    elif m < s and h < hP: short_v += 1
    if price < bbL[last] or price > bbM[last]: long_v += 1
    elif price > bbU[last] or price <= bbM[last]: short_v += 1
    direction = 'NEUTRAL'
    confluence = max(long_v, short_v)/5
    if long_v >= 4: direction = 'LONG'; confluence = long_v/5
    elif short_v >= 4: direction = 'SHORT'; confluence = short_v/5
    sl = tp1 = tp2 = None
    # Tighter SL (1x ATR), better TPs (1.5x and 3x ATR) for cleaner R:R
    if direction == 'LONG' and a:
        sl = price - 1.0*a; tp1 = price + 1.5*a; tp2 = price + 3.0*a
    elif direction == 'SHORT' and a:
        sl = price + 1.0*a; tp1 = price - 1.5*a; tp2 = price - 3.0*a
    return {'direction': direction, 'confluence': confluence, 'price': price,
            'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rsi': r}

def fetch_klines(pair, interval):
    url = f'https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    if data.get('error'): raise Exception(f"Kraken: {data['error']}")
    result = data['result']; arr = None
    for k, v in result.items():
        if k != 'last' and isinstance(v, list): arr = v; break
    if not arr: raise Exception('No OHLC data')
    return [{'ts': c[0]*1000, 'open': float(c[1]), 'high': float(c[2]),
             'low': float(c[3]), 'close': float(c[4])} for c in arr]

def send_telegram(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text,
               'parse_mode': 'HTML', 'disable_web_page_preview': True}
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"Telegram OK")
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"Telegram HTTP {e.code}: {e.read().decode()}")
        raise

def fmt_price(n):
    if n is None: return '—'
    if n >= 1000: return f'{n:,.2f}'
    if n >= 1: return f'{n:.4f}'
    return f'{n:.6f}'

def light(d):
    if d == 'LONG': return '🟢'
    if d == 'SHORT': return '🔴'
    return '🟡'

def pct_move(entry, target):
    """Return percentage move from entry to target (signed)."""
    return ((target - entry) / entry) * 100

def format_snapshot(rows):
    now = datetime.now(timezone.utc).strftime('%H:%M UTC')
    msg = f"<b>HWMF MARKET SNAPSHOT</b>\n"
    msg += f"<i>{now} · 15m candles</i>\n\n"

    for r in rows:
        if r.get('error'):
            msg += f"⚠️ <b>{r['symbol']}</b> error\n\n"
            continue
        sig = r['sig']; emoji = light(sig['direction'])
        pct = round(sig['confluence']*100)
        msg += f"{emoji} <b>{r['symbol']}</b>  ${fmt_price(sig['price'])}  ·  {sig['direction']} {pct}%  ·  RSI {sig['rsi']:.0f}\n"

        if sig['direction'] in ('LONG', 'SHORT') and sig['sl']:
            sl_pct = pct_move(sig['price'], sig['sl'])
            tp1_pct = pct_move(sig['price'], sig['tp1'])
            tp2_pct = pct_move(sig['price'], sig['tp2'])

            msg += f"   <code>Spot   SL {sl_pct:+.2f}%   TP1 {tp1_pct:+.2f}%   TP2 {tp2_pct:+.2f}%</code>\n"
            msg += f"   <code>5x     SL {sl_pct*5:+.2f}%   TP1 {tp1_pct*5:+.2f}%   TP2 {tp2_pct*5:+.2f}%</code>\n"
            msg += f"   <code>10x    SL {sl_pct*10:+.2f}%   TP1 {tp1_pct*10:+.2f}%   TP2 {tp2_pct*10:+.2f}%</code>\n"
        msg += "\n"

    msg += "<i>🟢 long  ·  🔴 short  ·  🟡 neutral</i>\n"
    msg += "<i>⚠ Leverage shown for reference. Higher leverage = higher liquidation risk.</i>"
    return msg

def main():
    rows = []
    print(f"Checking {len(PAIRS)} pairs at 15m candles...")
    for p in PAIRS:
        try:
            candles = fetch_klines(p['pair'], INTERVAL)
            sig = generate_signal(candles)
            if not sig:
                rows.append({'symbol': p['symbol'], 'error': 'no data'})
                continue
            rows.append({'symbol': p['symbol'], 'sig': sig})
            print(f"  {p['symbol']}: {sig['direction']} {round(sig['confluence']*100)}%")
        except Exception as e:
            rows.append({'symbol': p['symbol'], 'error': str(e)})
            print(f"  ERROR {p['symbol']}: {e}")
    print("Sending snapshot to Telegram...")
    send_telegram(format_snapshot(rows))
    print("Done. Snapshot posted.")

if __name__ == '__main__':
    main()
