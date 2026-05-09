# 🤖 MemeSignal Bot — Telegram Memecoin Signal Bot

Bot Telegram qui scanne en continu les nouveaux memecoins et envoie des signaux
quand un token a le potentiel de x2 (ou au moins un petit profit).

---

## 📋 Prérequis

- Compte [Render.com](https://render.com) (gratuit)
- Un bot Telegram créé via [@BotFather](https://t.me/BotFather)
- Ton Chat ID Telegram

---

## 🚀 Installation rapide

### 1. Créer ton bot Telegram

1. Ouvre Telegram → recherche **@BotFather**
2. Tape `/newbot`
3. Suis les instructions → tu reçois un **token** (ex: `7123456789:AAHxxxxxxxx`)
4. **Copie ce token**

### 2. Récupérer ton Chat ID

1. Envoie un message à ton bot
2. Va sur : `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
3. Trouve `"chat":{"id": XXXXXXXX}` → c'est ton Chat ID
   - Si c'est un groupe, le Chat ID commence par `-100`

### 3. Déployer sur Render

#### Option A — Depuis GitHub (recommandé)

1. **Push le code sur GitHub** :
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/TON_USER/memecoin-bot.git
   git push -u origin main
   ```

2. Sur [Render Dashboard](https://dashboard.render.com) :
   - Clique **New +** → **Web Service**
   - Connecte ton repo GitHub
   - Configure :
     | Champ | Valeur |
     |-------|--------|
     | Name | `memecoin-bot` |
     | Environment | `Python 3` |
     | Build Command | `pip install -r requirements.txt` |
     | Start Command | `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 bot:app` |
     | Instance Type | `Free` (ou `Starter` pour éviter le sleep) |

3. **Variables d'environnement** → clique **Add Environment Variable** :
   ```
   TELEGRAM_BOT_TOKEN   → ton token BotFather
   TELEGRAM_CHAT_ID     → ton chat ID
   MIN_LIQUIDITY        → 10000
   MIN_VOLUME_24H       → 5000
   MAX_TOKEN_AGE_H      → 24
   SCAN_INTERVAL        → 30
   MIN_SIGNAL_SCORE     → 65
   ```

4. Clique **Create Web Service** → le bot démarre !

#### Option B — Upload direct sur Render

1. Render Dashboard → **New +** → **Web Service**
2. Choisis **Upload files** et dépose les fichiers
3. Même configuration qu'au-dessus

---

## ⚙️ Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | **Obligatoire** — Token @BotFather |
| `TELEGRAM_CHAT_ID` | — | **Obligatoire** — Ton Chat ID |
| `MIN_LIQUIDITY` | `10000` | Liquidité minimale USD |
| `MIN_VOLUME_24H` | `5000` | Volume 24h minimal USD |
| `MAX_TOKEN_AGE_H` | `24` | Âge max du token (heures) |
| `SCAN_INTERVAL` | `30` | Secondes entre chaque scan |
| `MIN_SIGNAL_SCORE` | `65` | Score min pour envoyer (0-100) |

---

## 📊 Système de scoring

Le bot analyse chaque token et lui attribue un score de 0 à 100 :

| Critère | Points |
|---------|--------|
| Volume/Liquidité > 5x | +25 |
| Volume/Liquidité > 2x | +15 |
| > 70% des txns = achats | +20 |
| > 60% des txns = achats | +12 |
| Prix +20% sur 1h | +20 |
| Prix +10% sur 1h | +12 |
| Prix +50% sur 6h | +15 |
| Volume 1h > 3x la moyenne | +15 |
| Micro-cap < $100k | +10 |
| Liquidité healthy | +5 |

**Score ≥ 65** → Signal envoyé 🟡  
**Score ≥ 80** → Signal fort 🟢  

---

## 🔍 Sources de données

Le bot scanne via [DexScreener API](https://dexscreener.com) (gratuit, sans clé) :
- **Solana** — majoritairement memecoins
- **BSC** — BNB Chain
- **Ethereum** — ETH mainnet
- **Base** — L2 Coinbase

---

## 📡 Endpoints de monitoring

Une fois déployé, ton service expose :

- `GET /` → Statut complet (signaux envoyés, dernier scan, filtres)
- `GET /health` → Healthcheck simple

---

## ⚠️ Notes importantes

- **Plan gratuit Render** : le service "sleep" après 15min d'inactivité. Utilise un service comme [UptimeRobot](https://uptimerobot.com) (gratuit) pour pinger `/health` toutes les 5min et garder le bot actif.
- **Pas un conseil financier** : les signaux sont basés sur des métriques techniques, toujours faire son propre DYOR.
- **Risque crypto** : les memecoins sont extrêmement volatils, ne jamais investir plus que ce qu'on peut se permettre de perdre.
