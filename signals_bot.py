"""Builds26 Signal Desk Bot — individual signals + leverage + tracking + digest."""
import os, json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

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
INTERVAL = 60
EXPIRY_HOURS = 24
POSITION_SIZES = [100, 500, 1000]
LEVERAGE_LEVELS = [('Spot', 1), ('5x', 5), ('10x', 10)]
OPEN_FILE = 'open_signals.json'
HIST_FILE = 'signal_history.json'
META_FILE = 'bot_meta.json'

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
    sl = tp1 = tp2 = None; rr = None
    if direction == 'LONG' and a:
        sl = price - 1.0*a; tp1 = price + 1.5*a; tp2 = price + 3.0*a
        rr = (tp2 - price) / (price - sl)
    elif direction == 'SHORT' and a:
        sl = price + 1.0*a; tp1 = price - 1.5*a; tp2 = price - 3.0*a
        rr = (price - tp2) / (sl - price)
    return {'direction': direction, 'confluence': confluence, 'price': price,
            'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rsi': r, 'rr': rr}

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
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"Telegram HTTP {e.code}: {e.read().decode()}")
        raise

def fmt_price(n):
    if n is None: return '—'
    if n >= 1000: return f'{n:,.2f}'
    if n >= 1: return f'{n:.4f}'
    return f'{n:.6f}'

def fmt_money(n):
    sign = '+' if n >= 0 else '-'
    return f"{sign}${abs(n):,.2f}"

def pct_move(entry, target):
    return ((target - entry) / entry) * 100

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def make_signal_id(symbol, direction, ts):
    return f"{symbol}_{direction}_{ts}"

def open_new_signal(symbol, sig, candles):
    last_candle_ts = candles[-1]['ts']
    return {
        'id': make_signal_id(symbol, sig['direction'], last_candle_ts),
        'symbol': symbol,
        'direction': sig['direction'],
        'entry': sig['price'],
        'sl': sig['sl'],
        'tp1': sig['tp1'],
        'tp2': sig['tp2'],
        'rsi_at_open': sig['rsi'],
        'rr': sig['rr'],
        'confluence': sig['confluence'],
        'opened_ts': last_candle_ts,
        'opened_iso': datetime.fromtimestamp(last_candle_ts/1000, tz=timezone.utc).isoformat(),
        'tp1_hit': False,
    }

def check_resolution(open_sig, candles):
    relevant = [c for c in candles if c['ts'] > open_sig['opened_ts']]
    if not relevant:
        return None
    direction = open_sig['direction']
    sl = open_sig['sl']; tp1 = open_sig['tp1']; tp2 = open_sig['tp2']
    tp1_hit = open_sig.get('tp1_hit', False)
    for c in relevant:
        close = c['close']
        if direction == 'LONG':
            if close >= tp2:
                return ('TP2', tp2, c['ts'])
            if close <= sl:
                if tp1_hit:
                    return ('TP1_then_SL', sl, c['ts'])
                return ('SL', sl, c['ts'])
            if close >= tp1 and not tp1_hit:
                tp1_hit = True
                open_sig['tp1_hit'] = True
        else:
            if close <= tp2:
                return ('TP2', tp2, c['ts'])
            if close >= sl:
                if tp1_hit:
                    return ('TP1_then_SL', sl, c['ts'])
                return ('SL', sl, c['ts'])
            if close <= tp1 and not tp1_hit:
                tp1_hit = True
                open_sig['tp1_hit'] = True
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    age_hours = (now_ms - open_sig['opened_ts']) / (1000 * 3600)
    if age_hours >= EXPIRY_HOURS:
        last_close = relevant[-1]['close']
        status = 'TP1_EXPIRED' if tp1_hit else 'EXPIRED'
        return (status, last_close, relevant[-1]['ts'])
    return None
def format_new_signal(symbol, sig):
    """Mobile-friendly Telegram message for a fresh Strong signal."""
    emoji = '🟢' if sig['direction'] == 'LONG' else '🔴'
    pct = round(sig['confluence']*100)
    sl_p = pct_move(sig['price'], sig['sl'])
    tp1_p = pct_move(sig['price'], sig['tp1'])
    tp2_p = pct_move(sig['price'], sig['tp2'])
    if sig['direction'] == 'SHORT':
        sl_p = -sl_p
        tp1_p = -tp1_p
        tp2_p = -tp2_p
        tf_label = '1H' if INTERVAL == 60 else f'{INTERVAL}M' if INTERVAL < 60 else f'{INTERVAL//60}H'
    msg = f"{emoji} <b>BUILDS26 SIGNAL</b>\n"
    msg += f"<b>{symbol} {sig['direction']}</b>  ·  {pct}% confluence  ·  {tf_label}  ·  RSI {sig['rsi']:.0f}\n\n"

    msg += f"Entry:  <code>${fmt_price(sig['price'])}</code>\n"
    msg += f"SL:     <code>${fmt_price(sig['sl'])}</code>\n"
    msg += f"TP1:    <code>${fmt_price(sig['tp1'])}</code>\n"
    msg += f"TP2:    <code>${fmt_price(sig['tp2'])}</code>\n"
    if sig['rr']:
        msg += f"R:R 1:{sig['rr']:.2f}\n"
    for size in POSITION_SIZES:
        msg += f"\n━━━ <b>${size} position</b> ━━━\n"
        for label, lev in LEVERAGE_LEVELS:
            sl_pct_l = sl_p * lev
            tp1_pct_l = tp1_p * lev
            tp2_pct_l = tp2_p * lev
            sl_d = (sl_p/100) * size * lev
            tp1_d = (tp1_p/100) * size * lev
            tp2_d = (tp2_p/100) * size * lev
            msg += f"<b>{label}</b>\n"
            msg += f"<code>SL  {sl_pct_l:+6.2f}%   {fmt_money(sl_d)}</code>\n"
            msg += f"<code>TP1 {tp1_pct_l:+6.2f}%   {fmt_money(tp1_d)}</code>\n"
            msg += f"<code>TP2 {tp2_pct_l:+6.2f}%   {fmt_money(tp2_d)}</code>\n"
    msg += "\n<i>⚠ Higher leverage = higher liquidation risk.</i>\n"
    msg += "<i>Illustrative only. Excludes fees, slippage, funding.</i>"
    return msg

def format_resolution(open_sig, status, hit_price, hit_ts):
    entry = open_sig['entry']; symbol = open_sig['symbol']
    direction = open_sig['direction']
    duration_ms = hit_ts - open_sig['opened_ts']
    h = int(duration_ms / 3600000)
    m = int((duration_ms % 3600000) / 60000)
    duration = f"{h}h {m}m" if h else f"{m}m"
    pct = pct_move(entry, hit_price) if direction == 'LONG' else -pct_move(entry, hit_price)
    if status == 'TP2':
        head = f"✅ <b>{symbol} {direction}</b> hit TP2"
        verdict = "WIN"
    elif status == 'TP1_then_SL':
        head = f"⚖️ <b>{symbol} {direction}</b> hit TP1 then stopped"
        verdict = "BREAKEVEN"
    elif status == 'SL':
        head = f"❌ <b>{symbol} {direction}</b> stopped out"
        verdict = "LOSS"
    elif status == 'TP1_EXPIRED':
        head = f"⌛ <b>{symbol} {direction}</b> expired (TP1 hit earlier)"
        verdict = "PARTIAL"
    else:
        head = f"⌛ <b>{symbol} {direction}</b> expired (24h, no hit)"
        verdict = "FLAT"
    msg = f"{head}\n"
    msg += f"<i>Entry ${fmt_price(entry)} → Exit ${fmt_price(hit_price)}  ·  {duration}</i>\n\n"
    for size in POSITION_SIZES:
        msg += f"<b>${size}:</b> "
        parts = []
        for label, lev in LEVERAGE_LEVELS:
            d = (pct/100) * size * lev
            parts.append(f"{label} {fmt_money(d)}")
        msg += "  ·  ".join(parts) + "\n"
    return msg, verdict, pct

def format_daily_digest(open_signals, pair_candles):
    now = datetime.now(timezone.utc).strftime('%d %b %Y · %H:%M UTC')
        tf_label = '1H' if INTERVAL == 60 else f'{INTERVAL}M' if INTERVAL < 60 else f'{INTERVAL//60}H'
    msg = f"📊 <b>BUILDS26 DAILY DIGEST</b>\n<i>{now}  ·  {tf_label} candles</i>\n\n"

    if not open_signals:
        msg += "<i>No open signals right now. Bot is watching all 10 pairs and will alert when a Strong setup appears.</i>"
        return msg
    msg += f"<b>{len(open_signals)} open signal(s):</b>\n\n"
    for symbol, sig in open_signals.items():
        candles = pair_candles.get(symbol)
        cur_price = candles[-1]['close'] if candles else sig['entry']
        opened = datetime.fromisoformat(sig['opened_iso'])
        age_h = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        age = f"{int(age_h)}h" if age_h >= 1 else f"{int(age_h*60)}m"
        direction = sig['direction']; entry = sig['entry']
        progress_pct = pct_move(entry, cur_price) if direction == 'LONG' else -pct_move(entry, cur_price)
        if direction == 'LONG':
            to_tp1 = pct_move(cur_price, sig['tp1'])
            to_sl = pct_move(cur_price, sig['sl'])
        else:
            to_tp1 = -pct_move(cur_price, sig['tp1'])
            to_sl = -pct_move(cur_price, sig['sl'])
        emoji = '🟢' if direction == 'LONG' else '🔴'
        msg += f"{emoji} <b>{symbol} {direction}</b>  ({age} old)"
        if sig.get('tp1_hit'):
            msg += " · TP1 ✓"
        msg += "\n"
        msg += f"   Entry ${fmt_price(entry)} → now ${fmt_price(cur_price)} ({progress_pct:+.2f}%)\n"
        msg += f"   <i>{to_tp1:+.2f}% to TP1  ·  {to_sl:+.2f}% to SL</i>\n\n"
    return msg


def should_send_digest(meta):
    now = datetime.now(timezone.utc)
    today_str = now.strftime('%Y-%m-%d')
    last_digest_date = meta.get('last_digest_date')
    if last_digest_date == today_str:
        return False
    if now.hour == 8 and now.minute < 30:
        return True
    return False

def main():
    open_signals = load_json(OPEN_FILE, {})
    history = load_json(HIST_FILE, [])
    meta = load_json(META_FILE, {})
    pair_candles = {}
    new_signals_posted = 0
    resolutions_posted = 0

    print(f"Builds26 Bot · checking {len(PAIRS)} pairs at 15m candles...")

    for p in PAIRS:
        try:
            candles = fetch_klines(p['pair'], INTERVAL)
            pair_candles[p['symbol']] = candles
            sig = generate_signal(candles)
            if not sig:
                continue
            print(f"  {p['symbol']}: {sig['direction']} {round(sig['confluence']*100)}%")
            if (sig['direction'] in ('LONG', 'SHORT')
                    and sig['confluence'] >= 0.8
                    and p['symbol'] not in open_signals):
                new_sig = open_new_signal(p['symbol'], sig, candles)
                open_signals[p['symbol']] = new_sig
                try:
                    send_telegram(format_new_signal(p['symbol'], sig))
                    new_signals_posted += 1
                    print(f"  → POSTED new signal {p['symbol']} {sig['direction']}")
                except Exception as e:
                    print(f"  Failed to post new signal: {e}")
        except Exception as e:
            print(f"  ERROR {p['symbol']}: {e}")

    resolved_keys = []
    for symbol, open_sig in list(open_signals.items()):
        candles = pair_candles.get(symbol)
        if not candles:
            continue
        result = check_resolution(open_sig, candles)
        if result is None:
            continue
        status, hit_price, hit_ts = result
        already_resolved = any(h.get('id') == open_sig.get('id') for h in history)
        if already_resolved:
            print(f"  SKIP {symbol}: already in history, removing from open")
            resolved_keys.append(symbol)
            continue
        msg, verdict, pct = format_resolution(open_sig, status, hit_price, hit_ts)
        print(f"  RESOLVED {symbol}: {status} ({verdict}, {pct:+.2f}%)")
        history.append({
            **open_sig,
            'status': status,
            'verdict': verdict,
            'exit_price': hit_price,
            'exit_ts': hit_ts,
            'pct_spot': pct,
        })
        try:
            send_telegram(msg)
            resolutions_posted += 1
        except Exception as e:
            print(f"  Failed to send resolution: {e}")
        resolved_keys.append(symbol)

    for k in resolved_keys:
        del open_signals[k]

    if should_send_digest(meta):
        try:
            send_telegram(format_daily_digest(open_signals, pair_candles))
            meta['last_digest_date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            print("  → POSTED daily digest")
        except Exception as e:
            print(f"  Failed to send digest: {e}")

    save_json(OPEN_FILE, open_signals)
    save_json(HIST_FILE, history)
    save_json(META_FILE, meta)

    print(f"Done. New: {new_signals_posted}. Resolved: {resolutions_posted}. Open: {len(open_signals)}.")

if __name__ == '__main__':
    main()
