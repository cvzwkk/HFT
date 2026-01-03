import os
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
NGROK_AUTH_TOKEN = os.getenv("NGROK_KEY", "37fJKKexs66q3bWBAAelBYiU2Yp_7Fq6yLN25TUj43fiBHfEN")

WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"
BOOK_DEPTH = 25

INITIAL_BALANCE = 10_000.0
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05
FEE = 0.0004

IMBALANCE_THRESHOLD = 0.15
FORCE_CLOSE_PNL = 5.0

TRADE_HISTORY_LIMIT = 30

BASE_SPREAD_OFFSET = 0.15
K_IMB = 0.35
K_INV = 0.25

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

best_bid = 0.0
best_ask = 0.0

bid_levels = {}
ask_levels = {}

trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)
open_trades = []

html_content = ""

# =========================
# UTILITIES
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
    mid = mid_price()
    pnl = 0
    for t in open_trades:
        if t["side"] == "BUY":
            pnl += (mid - t["price"]) * t["qty"]
        else:
            pnl += (t["price"] - mid) * t["qty"]
    return pnl

# =========================
# EXECUTION (REALISTIC)
# =========================
def execute(side, qty, price):
    global balance, inventory, realized_pnl

    cost = price * qty
    fee = cost * FEE
    now = datetime.now(timezone.utc).isoformat()

    if side == "BUY":
        if balance < cost + fee:
            return
        balance -= cost + fee
        inventory += qty
        open_trades.append({"side": "BUY", "price": price, "qty": qty})

    else:
        if inventory < qty:
            return
        balance += cost - fee
        inventory -= qty

        for t in list(open_trades):
            if t["side"] == "BUY":
                pnl = (price - t["price"]) * qty
                realized_pnl += pnl
                trade_history.append({
                    "time": now,
                    "side": "SELL",
                    "entry": t["price"],
                    "exit": price,
                    "qty": qty,
                    "pnl": pnl
                })
                open_trades.remove(t)
                break

# =========================
# STRATEGY CORE (RAMM-SMM)
# =========================
def strategy_tick():
    if not best_bid or not best_ask:
        return

    imb = orderbook_imbalance()
    mid = mid_price()
    spread = best_ask - best_bid

    inv_adj = K_INV * inventory
    imb_adj = K_IMB * imb

    buy_price = best_bid - spread * (BASE_SPREAD_OFFSET + inv_adj - imb_adj)
    sell_price = best_ask + spread * (BASE_SPREAD_OFFSET - inv_adj + imb_adj)

    if imb > IMBALANCE_THRESHOLD and inventory + ORDER_SIZE <= MAX_INVENTORY:
        execute("BUY", ORDER_SIZE, buy_price)

    if imb < -IMBALANCE_THRESHOLD and inventory - ORDER_SIZE >= -MAX_INVENTORY:
        execute("SELL", ORDER_SIZE, sell_price)

    if unrealized_pnl() >= FORCE_CLOSE_PNL:
        close_all(mid)

def close_all(price):
    global open_trades
    for t in list(open_trades):
        execute("SELL", t["qty"], price)
    open_trades = []

# =========================
# HTML
# =========================
def generate_html():
    global html_content

    html = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="2">
        <style>
            body {{ background:#111; color:#0f0; font-family: monospace; }}
            table {{ width:100%; border-collapse:collapse; }}
            th,td {{ border:1px solid #0f0; padding:4px; text-align:center; }}
        </style>
    </head>
    <body>
        <h2>RAMM-SMM-L2 — Bitfinex</h2>
        <p>
            Balance: {balance:.2f} |
            Inventory: {inventory:.4f} BTC |
            Realized PnL: {realized_pnl:.2f} |
            Unrealized PnL: {unrealized_pnl():.2f}
        </p>
        <p>
            Best Bid: {best_bid} |
            Best Ask: {best_ask} |
            Mid: {mid_price():.2f}
        </p>
        <h3>Trade History</h3>
        <table>
            <tr>
                <th>Time</th><th>Side</th><th>Entry</th>
                <th>Exit</th><th>Qty</th><th>PnL</th>
            </tr>
    """

    for t in trade_history:
        html += f"""
        <tr>
            <td>{t['time']}</td>
            <td>{t['side']}</td>
            <td>{t['entry']:.2f}</td>
            <td>{t['exit']:.2f}</td>
            <td>{t['qty']}</td>
            <td>{t['pnl']:.2f}</td>
        </tr>
        """

    html += "</table></body></html>"
    html_content = html

# =========================
# HTTP SERVER
# =========================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html_content.encode())

def start_http():
    public = ngrok.connect(5009)
    print("🌍 Public URL:", public)
    HTTPServer(("0.0.0.0", 5009), Handler).serve_forever()

# =========================
# BITFINEX WS
# =========================
async def ws_loop():
    global best_bid, best_ask

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

            # ignora mensagens de evento
            if isinstance(data, dict):
                continue

            # heartbeat
            if data[1] == "hb":
                continue

            payload = data[1]

            # =========================
            # SNAPSHOT
            # =========================
            if isinstance(payload, list) and isinstance(payload[0], list):
                for price, count, amount in payload:
                    if amount > 0:
                        bid_levels[price] = amount
                        ask_levels.pop(price, None)
                    else:
                        ask_levels[price] = abs(amount)
                        bid_levels.pop(price, None)

            # =========================
            # DELTA UPDATE
            # =========================
            elif isinstance(payload, list) and len(payload) == 3:
                price, count, amount = payload

                if count == 0:
                    bid_levels.pop(price, None)
                    ask_levels.pop(price, None)
                elif amount > 0:
                    bid_levels[price] = amount
                    ask_levels.pop(price, None)
                else:
                    ask_levels[price] = abs(amount)
                    bid_levels.pop(price, None)

            # =========================
            # BEST PRICES
            # =========================
            if bid_levels and ask_levels:
                best_bid = max(bid_levels)
                best_ask = min(ask_levels)

            # =========================
            # STRATEGY + UI
            # =========================
            strategy_tick()
            generate_html()

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
