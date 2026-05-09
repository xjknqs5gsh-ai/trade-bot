import os, sys, time, requests, logging, threading
from datetime import datetime, timezone
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout, force=True)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MIN_LIQUIDITY = float(os.environ.get("MIN_LIQUIDITY", 10000))
MIN_VOLUME_24H = float(os.environ.get("MIN_VOLUME_24H", 5000))
MAX_TOKEN_AGE_H = float(os.environ.get("MAX_TOKEN_AGE_H", 24))
SCAN_INTERVAL = float(os.environ.get("SCAN_INTERVAL", 30))
MIN_SIGNAL_SCORE = float(os.environ.get("MIN_SIGNAL_SCORE", 65))

app = Flask(__name__)
sent_tokens = set()
signal_count = 0
last_scan = None

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Token ou Chat ID manquant")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
        if r.status_code == 200:
            log.info("Telegram OK")
            return True
        log.error("Telegram erreur %s: %s", r.status_code, r.text)
    except Exception as e:
        log.error("Telegram exception: %s", e)
    return False

def score_token(pair):
    score = 0
    reasons = []
    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
    vol24 = pair.get("volume", {}).get("h24", 0) or 0
    vol1 = pair.get("volume", {}).get("h1", 0) or 0
    buys24 = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells24 = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    price_ch1 = pair.get("priceChange", {}).get("h1", 0) or 0
    price_ch6 = pair.get("priceChange", {}).get("h6", 0) or 0
    mktcap = pair.get("marketCap", 0) or 0
    vlr = vol24 / liquidity if liquidity > 0 else 0
    if vlr > 5:
        score += 25
        reasons.append("Feu Volume/Liq x" + str(round(vlr, 1)))
    elif vlr > 2:
        score += 15
        reasons.append("Volume/Liq x" + str(round(vlr, 1)))
    elif vlr > 1:
        score += 8
    total = buys24 + sells24
    if total > 0:
        bp = buys24 / total
        if bp > 0.70:
            score += 20
            reasons.append(str(round(bp * 100)) + "% achats bull fort")
        elif bp > 0.60:
            score += 12
            reasons.append(str(round(bp * 100)) + "% achats")
        elif bp > 0.50:
            score += 5
    if price_ch1 > 20:
        score += 20
        reasons.append("+" + str(round(price_ch1, 1)) + "% sur 1h")
    elif price_ch1 > 10:
        score += 12
        reasons.append("+" + str(round(price_ch1, 1)) + "% sur 1h")
    elif price_ch1 > 5:
        score += 6
    elif price_ch1 < -10:
        score -= 10
    if price_ch6 > 50:
        score += 15
        reasons.append("+" + str(round(price_ch6, 1)) + "% sur 6h")
    elif price_ch6 > 20:
        score += 8
    avg = vol24 / 24 if vol24 > 0 else 0
    if avg > 0 and vol1 > avg * 3:
        score += 15
        reasons.append("Vol 1h x" + str(round(vol1 / avg, 1)) + " vs moyenne")
    elif avg > 0 and vol1 > avg * 1.5:
        score += 7
    if 0 < mktcap < 100000:
        score += 10
        reasons.append("Micro-cap $" + str(round(mktcap)))
    elif 0 < mktcap < 500000:
        score += 6
        reasons.append("Small-cap $" + str(round(mktcap)))
    if 20000 < liquidity < 200000:
        score += 5
        reasons.append("Liquidite saine $" + str(round(liquidity)))
    return min(score, 100), reasons

def passes_filters(pair):
    liq = pair.get("liquidity", {}).get("usd", 0) or 0
    vol = pair.get("volume", {}).get("h24", 0) or 0
    crea = pair.get("pairCreatedAt")
    if liq < MIN_LIQUIDITY:
        return False, "Liq faible"
    if vol < MIN_VOLUME_24H:
        return False, "Vol faible"
    if crea:
        age_h = (time.time() * 1000 - crea) / 3600000
        if age_h > MAX_TOKEN_AGE_H:
            return False, "Trop vieux"
    return True, "OK"

def format_signal(pair, score, reasons):
    name = pair.get("baseToken", {}).get("name", "?")
    sym = pair.get("baseToken", {}).get("symbol", "?")
    addr = pair.get("baseToken", {}).get("address", "")
    chain = pair.get("chainId", "?").upper()
    dex = pair.get("dexId", "?").capitalize()
    price = pair.get("priceUsd", "0")
    liq = pair.get("liquidity", {}).get("usd", 0) or 0
    v24 = pair.get("volume", {}).get("h24", 0) or 0
    v1 = pair.get("volume", {}).get("h1", 0) or 0
    b24 = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    s24 = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    c1 = pair.get("priceChange", {}).get("h1", 0) or 0
    c6 = pair.get("priceChange", {}).get("h6", 0) or 0
    c24 = pair.get("priceChange", {}).get("h24", 0) or 0
    mc = pair.get("marketCap", 0) or 0
    url = pair.get("url", "")
    crea = pair.get("pairCreatedAt")
    age = str(round((time.time() * 1000 - crea) / 3600000, 1)) + "h" if crea else "?"
    bar = "X" * int(score / 10) + "." * (10 - int(score / 10))
    em = "FORT" if score >= 80 else "MOYEN"
    rsn = "\n".join("  - " + r for r in reasons) or "  - Criteres de base atteints"
    msg = (
        em + " SIGNAL MEMECOIN\n"
        "------------------------\n"
        "Nom: " + name + " ($" + sym + ")\n"
        "Chain: " + chain + " | DEX: " + dex + " | Age: " + age + "\n"
        "Prix: $" + str(price) + "\n"
        "1h: " + str(round(c1, 1)) + "% | 6h: " + str(round(c6, 1)) + "% | 24h: " + str(round(c24, 1)) + "%\n"
        "Liquidite: $" + str(round(liq)) + "\n"
        "Volume 24h: $" + str(round(v24)) + " | Volume 1h: $" + str(round(v1)) + "\n"
        "MarketCap: $" + str(round(mc)) + "\n"
        "Txns: " + str(b24) + " achats / " + str(s24) + " ventes\n"
        "Score: [" + bar + "] " + str(score) + "/100\n"
        + rsn + "\n"
        "------------------------\n"
        "DYOR - Pas un conseil financier"
    )
    return msg

def fetch_new_pairs():
    all_pairs = []
    for chain in ["solana", "bsc", "ethereum", "base"]:
        try:
            r = requests.get("https://api.dexscreener.com/latest/dex/tokens/trending/" + chain, timeout=10)
            if r.status_code == 200:
                pairs = r.json().get("pairs", []) or []
                all_pairs.extend(pairs)
                log.info("Chain %s: %d pairs", chain, len(pairs))
        except Exception as e:
            log.error("Erreur %s: %s", chain, e)
    if not all_pairs:
        try:
            r = requests.get("https://api.dexscreener.com/latest/dex/search?q=memecoin", timeout=10)
            if r.status_code == 200:
                all_pairs = r.json().get("pairs", []) or []
        except Exception as e:
            log.error("Erreur fallback: %s", e)
    return all_pairs

def scan_loop():
    global signal_count, last_scan
    log.info("Bot demarre - scan toutes les %s secondes", SCAN_INTERVAL)
    send_telegram(
        "Bot MemeSignal demarre !\n\n"
        "Liquidite min: $" + str(round(MIN_LIQUIDITY)) + "\n"
        "Volume min: $" + str(round(MIN_VOLUME_24H)) + "\n"
        "Age max: " + str(MAX_TOKEN_AGE_H) + "h\n"
        "Score min: " + str(MIN_SIGNAL_SCORE) + "/100\n"
        "Intervalle: " + str(SCAN_INTERVAL) + "s\n\n"
        "Surveillance active..."
    )
    while True:
        last_scan = datetime.now(timezone.utc).isoformat()
        try:
            pairs = fetch_new_pairs()
            log.info("Total: %d pairs analyses", len(pairs))
            for pair in pairs:
                addr = pair.get("pairAddress", "")
                if not addr or addr in sent_tokens:
                    continue
                ok, _ = passes_filters(pair)
                if not ok:
                    continue
                score, reasons = score_token(pair)
                if score < MIN_SIGNAL_SCORE:
                    continue
                msg = format_signal(pair, score, reasons)
                if send_telegram(msg):
                    sent_tokens.add(addr)
                    signal_count += 1
                    log.info("Signal #%d envoye: %s score=%d", signal_count, pair.get("baseToken", {}).get("symbol", "?"), score)
                    if len(sent_tokens) > 5000:
                        sent_tokens.clear()
        except Exception as e:
            log.error("Erreur scan: %s", e)
        time.sleep(SCAN_INTERVAL)

@app.route("/")
def index():
    return jsonify({"status": "running", "signals": signal_count, "last_scan": last_scan})

@app.route("/health")
def health():
    return jsonify({"ok": True})

log.info("Demarrage du thread bot...")
threading.Thread(target=scan_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
