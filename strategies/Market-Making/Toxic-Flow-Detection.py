import asyncio
import json
import random
import time
from datetime import datetime, timezone
from collections import deque
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import websockets
from pyngrok import ngrok, conf
import nest_asyncio

# =========================
# CONFIG
# =========================
NGROK_AUTH_TOKEN = "37fJKKexs66q3bWBAAelBYiU2Yp_7Fq6yLN25TUj43fiBHfEN"
WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"
BOOK_DEPTH = 25

INITIAL_BALANCE = 10000.0
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05
FEE = 0.0004

IMBALANCE_THRESHOLD = 0.2
TOXICITY_LIMIT = -2.0
FORCE_CLOSE_UNREALIZED = 5.0

IGNORE_INITIAL_TRADES = 20
HTML_REFRESH = 2

# =========================
# NGROK
# =========================
conf.get_default().auth_token = NGROK_AUTH_TOKEN

# =========================
# GLOBAL STATE
# =========================
balance = INITIAL_BALANCE
inventory = 0.0
realized_pnl = 0.0

bid_levels = {}
ask_levels = {}

best_bid = best_ask = 0.0

trade_history = deque(maxlen=50)
toxicity_window = deque(maxlen=20)

processed_trades = 0
html_content = ""

# =========================
# UTILS
# =========================
def mid_price():
    return (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0

def orderbook_imbalance():
    bid_vol = sum(bid_levels.values())
    ask_vol = sum(ask_levels.values())
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)

def unrealized_pnl():
    if not trade_history:
        return 0
    last_buy = next((t for t in reversed(trade_history)
                     if t["side"] == "BUY" and not t["closed"]), None)
    if not last_buy:
        return 0
    return (mid_price() - last_buy["price"]) * last_buy["qty"]

def toxicity_index():
    return sum(toxicity_window) / len(toxicity_window) if toxicity_window else 0

# =========================
# EXECUTION
# =========================
def execute_trade(side, qty):
    global balance, inventory, realized_pnl, processed_trades

    if processed_trades < IGNORE_INITIAL_TRADES:
        processed_trades += 1
        return

    price = best_ask if side == "BUY" else best_bid
    if price == 0:
        return

    cost = price * qty
    fee = cost * FEE

    if side == "BUY":
        if balance < cost + fee:
            return
        balance -= cost + fee
        inventory += qty

        trade_history.append({
            "side": "BUY",
            "price": price,
            "qty": qty,
            "time": datetime.now(timezone.utc).isoformat(),
            "closed": False
        })

    else:
        if inventory < qty:
            return
        balance += cost - fee
        inventory -= qty

        for t in reversed(trade_history):
            if t["side"] == "BUY" and not t["closed"]:
                pnl = (price - t["price"]) * qty
                realized_pnl += pnl
                t["closed"] = True
                toxicity_window.append(pnl)
                break

        trade_history.append({
            "side": "SELL",
            "price": price,
            "qty": qty,
            "time": datetime.now(timezone.utc).isoformat(),
            "closed": True
        })

# =========================
# CORE STRATEGY
# =========================
def evaluate_strategy():
    imbalance = orderbook_imbalance()
    tox = toxicity_index()
    mid = mid_price()

    # 🚨 Forced close on profit
    if unrealized_pnl() >= FORCE_CLOSE_UNREALIZED:
        execute_trade("SELL", ORDER_SIZE)
        return

    # ☠️ Toxic flow filter
    if tox < TOXICITY_LIMIT:
        return

    if imbalance > IMBALANCE_THRESHOLD and inventory + ORDER_SIZE <= MAX_INVENTORY:
        execute_trade("BUY", ORDER_SIZE)

    elif imbalance < -IMBALANCE_THRESHOLD and inventory - ORDER_SIZE >= 0:
        execute_trade("SELL", ORDER_SIZE)

# =========================
# HTML
# =========================
def generate_html():
    global html_content
    html_content = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="{HTML_REFRESH}">
        <style>
            body {{ background:#111; color:#0f0; font-family:monospace }}
            table {{ border-collapse: collapse; width:100% }}
            td, th {{ border:1px solid #0f0; padding:4px }}
        </style>
    </head>
    <body>
        <h2>TFD-RAMM — Toxic Flow Detection</h2>
        <p>
        Balance: {balance:.2f} USD |
        Inventory: {inventory:.4f} BTC |
        Realized PnL: {realized_pnl:.2f} |
        Unrealized PnL: {unrealized_pnl():.2f}
        </p>
        <p>
        Best Bid: {best_bid} |
        Best Ask: {best_ask} |
        Mid: {mid_price():.2f}
        </p>
        <p>
        Imbalance: {orderbook_imbalance():.3f} |
        Toxicity Index: {toxicity_index():.3f}
        </p>
    </body>
    </html>
    """

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html_content.encode())

def start_http():
    url = ngrok.connect(8000)
    print("🌍 HTTP:", url)
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()

# =========================
# BITFINEX WS
# =========================
async def ws_loop():
    global bid_levels, ask_levels, best_bid, best_ask

    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "event": "subscribe",
            "channel": "book",
            "symbol": SYMBOL,
            "prec": "P0",
            "freq": "F0",
            "len": BOOK_DEPTH
        }))

        async for msg in ws:
            data = json.loads(msg)
            if not isinstance(data, list) or len(data) < 2:
                continue

            book = data[1]
            if isinstance(book[0], list):
                for price, count, amount in book:
                    if count == 0:
                        bid_levels.pop(price, None)
                        ask_levels.pop(price, None)
                    elif amount > 0:
                        bid_levels[price] = amount
                    else:
                        ask_levels[price] = abs(amount)

            if bid_levels and ask_levels:
                best_bid = max(bid_levels)
                best_ask = min(ask_levels)
                evaluate_strategy()
                generate_html()

# =========================
# START
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
