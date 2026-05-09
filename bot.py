import os
import time
import requests
import logging
from datetime import datetime, timezone
from flask import Flask, jsonify
import threading

# ─── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

MIN_LIQUIDITY      = float(os.environ.get("MIN_LIQUIDITY", 10000))
MIN_VOLUME_24H     = float(os.environ.get("MIN_VOLUME_24H", 5000))
MAX_TOKEN_AGE_H    = float(os.environ.get("MAX_TOKEN_AGE_H", 24))
SCAN_INTERVAL      = float(os.environ.get("SCAN_INTERVAL", 30))

# Score minimal pour envoyer un signal (sur 100)
MIN_SIGNAL_SCORE   = float(os.environ.get("MIN_SIGNAL_SCORE", 65))

DEXSCREENER_API    = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q="

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─── État global ──────────────────────────────────────────────────────────────
sent_tokens   = set()   # évite les doublons
signal_count  = 0
last_scan     = None
bot_running   = False


# ─── Helpers Telegram ─────────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Tokens Telegram manquants.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True
        log.error("Telegram error %s: %s", r.status_code, r.text)
    except Exception as e:
        log.error("Telegram exception: %s", e)
    return False


# ─── Scoring ──────────────────────────────────────────────────────────────────
def score_token(pair: dict) -> tuple[int, list[str]]:
    """
    Retourne (score 0-100, liste de raisons bullish).
    Plus le score est élevé, plus le token a de chances de x2.
    """
    score   = 0
    reasons = []

    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
    vol24     = pair.get("volume", {}).get("h24", 0) or 0
    vol6      = pair.get("volume", {}).get("h6", 0) or 0
    vol1      = pair.get("volume", {}).get("h1", 0) or 0
    buys24    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells24   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    price_ch1 = pair.get("priceChange", {}).get("h1", 0) or 0
    price_ch6 = pair.get("priceChange", {}).get("h6", 0) or 0
    fdv       = pair.get("fdv", 0) or 0
    mktcap    = pair.get("marketCap", 0) or 0

    # --- Volume/Liquidité ratio (momentum)
    vol_liq_ratio = vol24 / liquidity if liquidity > 0 else 0
    if vol_liq_ratio > 5:
        score += 25
        reasons.append(f"🔥 Volume/Liq x{vol_liq_ratio:.1f} (très actif)")
    elif vol_liq_ratio > 2:
        score += 15
        reasons.append(f"📈 Volume/Liq x{vol_liq_ratio:.1f}")
    elif vol_liq_ratio > 1:
        score += 8

    # --- Pression d'achat
    total_txns = buys24 + sells24
    if total_txns > 0:
        buy_pressure = buys24 / total_txns
        if buy_pressure > 0.70:
            score += 20
            reasons.append(f"💚 {buy_pressure*100:.0f}% achats (forte pression bull)")
        elif buy_pressure > 0.60:
            score += 12
            reasons.append(f"🟢 {buy_pressure*100:.0f}% achats")
        elif buy_pressure > 0.50:
            score += 5

    # --- Momentum prix récent
    if price_ch1 > 20:
        score += 20
        reasons.append(f"🚀 +{price_ch1:.1f}% sur 1h (momentum fort)")
    elif price_ch1 > 10:
        score += 12
        reasons.append(f"📊 +{price_ch1:.1f}% sur 1h")
    elif price_ch1 > 5:
        score += 6
    elif price_ch1 < -10:
        score -= 10  # pénalité dump

    if price_ch6 > 50:
        score += 15
        reasons.append(f"⚡ +{price_ch6:.1f}% sur 6h (trend haussier)")
    elif price_ch6 > 20:
        score += 8

    # --- Accélération du volume (vol 1h vs moyenne)
    avg_vol_h = vol24 / 24 if vol24 > 0 else 0
    if avg_vol_h > 0 and vol1 > avg_vol_h * 3:
        score += 15
        reasons.append(f"⚡ Volume 1h x{vol1/avg_vol_h:.1f} vs moyenne (accélération)")
    elif avg_vol_h > 0 and vol1 > avg_vol_h * 1.5:
        score += 7

    # --- Market cap faible = plus de potentiel x2
    if 0 < mktcap < 100_000:
        score += 10
        reasons.append(f"💎 Micro-cap ${mktcap:,.0f} (potentiel x2+ élevé)")
    elif 0 < mktcap < 500_000:
        score += 6
        reasons.append(f"🔹 Small-cap ${mktcap:,.0f}")

    # --- Liquidité healthy (ni trop basse ni top)
    if 20_000 < liquidity < 200_000:
        score += 5
        reasons.append(f"✅ Liquidité saine ${liquidity:,.0f}")

    return min(score, 100), reasons


# ─── Filtres de base ──────────────────────────────────────────────────────────
def passes_filters(pair: dict) -> tuple[bool, str]:
    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
    vol24     = pair.get("volume", {}).get("h24", 0) or 0
    created   = pair.get("pairCreatedAt")           # timestamp ms

    if liquidity < MIN_LIQUIDITY:
        return False, f"Liquidité trop faible (${liquidity:,.0f})"

    if vol24 < MIN_VOLUME_24H:
        return False, f"Volume trop faible (${vol24:,.0f})"

    if created:
        age_h = (time.time() * 1000 - created) / 3_600_000
        if age_h > MAX_TOKEN_AGE_H:
            return False, f"Token trop vieux ({age_h:.1f}h)"

    return True, "OK"


# ─── Formateur de signal ──────────────────────────────────────────────────────
def format_signal(pair: dict, score: int, reasons: list[str]) -> str:
    name      = pair.get("baseToken", {}).get("name", "?")
    symbol    = pair.get("baseToken", {}).get("symbol", "?")
    address   = pair.get("baseToken", {}).get("address", "")
    chain     = pair.get("chainId", "?").upper()
    dex       = pair.get("dexId", "?").capitalize()
    price     = pair.get("priceUsd", "0")
    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
    vol24     = pair.get("volume", {}).get("h24", 0) or 0
    vol1      = pair.get("volume", {}).get("h1", 0) or 0
    buys24    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells24   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    price_ch1 = pair.get("priceChange", {}).get("h1", 0) or 0
    price_ch6 = pair.get("priceChange", {}).get("h6", 0) or 0
    price_ch24= pair.get("priceChange", {}).get("h24", 0) or 0
    mktcap    = pair.get("marketCap", 0) or 0
    pair_url  = pair.get("url", "")
    created   = pair.get("pairCreatedAt")

    age_str = "?"
    if created:
        age_h = (time.time() * 1000 - created) / 3_600_000
        if age_h < 1:
            age_str = f"{age_h*60:.0f}min"
        else:
            age_str = f"{age_h:.1f}h"

    # Barre score visuelle
    filled  = int(score / 10)
    bar     = "█" * filled + "░" * (10 - filled)
    emoji   = "🟢" if score >= 80 else "🟡" if score >= 65 else "🔴"

    reasons_text = "\n".join(f"  • {r}" for r in reasons) if reasons else "  • Critères de base atteints"

    msg = f"""
{emoji} <b>SIGNAL MEMECOIN DÉTECTÉ</b> {emoji}
━━━━━━━━━━━━━━━━━━━━━━━━━━

🪙 <b>{name}</b> (<code>${symbol}</code>)
🔗 Chain : <b>{chain}</b> | DEX : <b>{dex}</b>
⏰ Âge : <b>{age_str}</b>

💰 Prix : <b>${price}</b>
📊 Évolution :
  └ 1h : <b>{price_ch1:+.2f}%</b>
  └ 6h : <b>{price_ch6:+.2f}%</b>
  └ 24h: <b>{price_ch24:+.2f}%</b>

💧 Liquidité : <b>${liquidity:,.0f}</b>
📦 Volume 24h : <b>${vol24:,.0f}</b>
📦 Volume 1h  : <b>${vol1:,.0f}</b>
🏦 Market Cap : <b>${mktcap:,.0f}</b>
🔄 Txns 24h : <b>{buys24}B / {sells24}S</b>

⚡ SCORE SIGNAL : {bar} <b>{score}/100</b>
📌 Raisons :
{reasons_text}

🔍 <a href="{pair_url}">Voir sur DexScreener</a>
<code>{address[:20]}...</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>DYOR — Pas un conseil financier</i>
""".strip()
    return msg


# ─── Récupération des tokens ──────────────────────────────────────────────────
def fetch_new_pairs() -> list[dict]:
    """Récupère les derniers pairs via DexScreener."""
    chains = ["solana", "bsc", "ethereum", "base"]
    all_pairs = []

    for chain in chains:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/trending/{chain}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", []) or []
                all_pairs.extend(pairs)
        except Exception as e:
            log.error("Erreur fetch %s: %s", chain, e)

    # Fallback : endpoint générique
    if not all_pairs:
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=memecoin"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                all_pairs = r.json().get("pairs", []) or []
        except Exception as e:
            log.error("Erreur fallback: %s", e)

    return all_pairs


# ─── Boucle principale ────────────────────────────────────────────────────────
def scan_loop():
    global signal_count, last_scan, bot_running
    log.info("🤖 Bot démarré — scan toutes les %.0fs", SCAN_INTERVAL)

    send_telegram(
        "🤖 <b>MemeSignal Bot démarré !</b>\n\n"
        f"⚙️ Filtres actifs :\n"
        f"  • Liquidité min : <b>${MIN_LIQUIDITY:,.0f}</b>\n"
        f"  • Volume min : <b>${MIN_VOLUME_24H:,.0f}</b>\n"
        f"  • Âge max : <b>{MAX_TOKEN_AGE_H}h</b>\n"
        f"  • Intervalle : <b>{SCAN_INTERVAL}s</b>\n"
        f"  • Score min : <b>{MIN_SIGNAL_SCORE}/100</b>\n\n"
        "📡 Surveillance en cours..."
    )

    while bot_running:
        last_scan = datetime.now(timezone.utc).isoformat()
        try:
            pairs = fetch_new_pairs()
            log.info("Pairs récupérés: %d", len(pairs))

            for pair in pairs:
                pair_addr = pair.get("pairAddress", "")
                if not pair_addr or pair_addr in sent_tokens:
                    continue

                ok, reason = passes_filters(pair)
                if not ok:
                    continue

                score, reasons = score_token(pair)
                if score < MIN_SIGNAL_SCORE:
                    log.debug("Score insuffisant %d pour %s", score,
                              pair.get("baseToken", {}).get("symbol", "?"))
                    continue

                msg = format_signal(pair, score, reasons)
                if send_telegram(msg):
                    sent_tokens.add(pair_addr)
                    signal_count += 1
                    log.info("✅ Signal envoyé: %s (score %d)",
                             pair.get("baseToken", {}).get("symbol", "?"), score)
                    # Garder le set en mémoire raisonnable
                    if len(sent_tokens) > 5000:
                        sent_tokens.clear()

        except Exception as e:
            log.error("Erreur scan_loop: %s", e)

        time.sleep(SCAN_INTERVAL)


# ─── Routes Flask (Render web service keepalive) ──────────────────────────────
@app.route("/")
def index():
    return jsonify({
        "status":       "running",
        "signal_count": signal_count,
        "last_scan":    last_scan,
        "filters": {
            "min_liquidity":   MIN_LIQUIDITY,
            "min_volume_24h":  MIN_VOLUME_24H,
            "max_token_age_h": MAX_TOKEN_AGE_H,
            "scan_interval_s": SCAN_INTERVAL,
            "min_score":       MIN_SIGNAL_SCORE,
        }
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "signals_sent": signal_count})


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot_running = True
    t = threading.Thread(target=scan_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
