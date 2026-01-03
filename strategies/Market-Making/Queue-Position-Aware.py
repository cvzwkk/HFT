import asyncio
import json
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
FEE = 0.0004
MAX_INVENTORY = 0.05

IMBALANCE_THRESHOLD = 0.18
UNREALIZED_FORCE_CLOSE = 5.0  # USD
TRAILING_PCT = 0.001          # 0.1%

HTML_PORT = 5009
HTML_REFRESH = 2
TRADE_HISTORY_LIMIT = 25

# =========================
# NGROK AUTH
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

best_bid = 0.0
best_ask = 0.0

trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)
open_trade = None
trailing_stop = None

html_content = ""

# =========================
# UTILITIES
# =========================
def mid_price():
    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    return 0.0


def book_imbalance():
    bid_vol = sum(list(bid_levels.values())[:BOOK_DEPTH])
    ask_vol = sum(list(ask_levels.values())[:BOOK_DEPTH])
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)


def unrealized_pnl():
    if not open_trade:
        return 0.0
    side = open_trade["side"]
    entry = open_trade["entry"]
    qty = open_trade["qty"]
    mid = mid_price()
    if side == "BUY":
        return (mid - entry) * qty
    else:
        return (entry - mid) * qty


# =========================
# EXECUTION (REAL L2 FILLS)
# =========================
def execute(side):
    global balance, inventory, realized_pnl
    global open_trade, trailing_stop

    if side == "BUY":
        if balance < best_ask * ORDER_SIZE:
            return
        price = best_ask
        cost = price * ORDER_SIZE * (1 + FEE)
        balance -= cost
        inventory += ORDER_SIZE
        open_trade = {
            "side": "BUY",
            "entry": price,
            "qty": ORDER_SIZE,
            "time": datetime.now(timezone.utc).isoformat()
        }
        trailing_stop = price * (1 - TRAILING_PCT)

    elif side == "SELL":
        if inventory < ORDER_SIZE:
            return
        price = best_bid
        proceeds = price * ORDER_SIZE * (1 - FEE)
        balance += proceeds
        inventory -= ORDER_SIZE

        if open_trade:
            pnl = (price - open_trade["entry"]) * ORDER_SIZE
            realized_pnl += pnl
            trade_history.append({
                "side": "BUY→SELL",
                "entry": open_trade["entry"],
                "exit": price,
                "qty": ORDER_SIZE,
                "pnl": pnl,
                "time": datetime.now(timezone.utc).isoformat()
            })
            open_trade = None
            trailing_stop = None


# =========================
# STRATEGY CORE
# =========================
def strategy_tick():
    global trailing_stop

    mid = mid_price()
    imb = book_imbalance()
    u_pnl = unrealized_pnl()

    # Force close on unrealized target
    if open_trade and u_pnl >= UNREALIZED_FORCE_CLOSE:
        execute("SELL")
        return

    # Trailing stop
    if open_trade and mid <= trailing_stop:
        execute("SELL")
        return

    # Entry logic
    if not open_trade:
        if imb > IMBALANCE_THRESHOLD and inventory + ORDER_SIZE <= MAX_INVENTORY:
            execute("BUY")

    # Update trailing
    if open_trade:
        trailing_stop = max(trailing_stop, mid * (1 - TRAILING_PCT))


# =========================
# HTML
# =========================
def generate_html():
    global html_content

    html = f"""
    <html>
    <head>
      <meta http-equiv="refresh" content="{HTML_REFRESH}">
      <style>
        body {{ background:#111;color:#0f0;font-family:monospace }}
        table {{ width:100%; border-collapse:collapse }}
        td,th {{ border:1px solid #0f0;padding:4px }}
      </style>
    </head>
    <body>
    <h2>QPA-MM Bitfinex L2</h2>
    <p>Balance: {balance:.2f} USD | Inventory: {inventory:.4f} BTC</p>
    <p>Realized PnL: {realized_pnl:.2f} | Unrealized: {unrealized_pnl():.2f}</p>
    <p>Best Bid: {best_bid} | Best Ask: {best_ask} | Mid: {mid_price():.2f}</p>

    <h3>Trades</h3>
    <table>
    <tr><th>Time</th><th>Entry</th><th>Exit</th><th>Qty</th><th>PnL</th></tr>
    """

    for t in trade_history:
        html += f"<tr><td>{t['time']}</td><td>{t['entry']}</td><td>{t['exit']}</td><td>{t['qty']}</td><td>{t['pnl']:.2f}</td></tr>"

    html += "</table></body></html>"
    html_content = html


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html_content.encode())


def start_http():
    public = ngrok.connect(HTML_PORT)
    print("🌍 NGROK:", public)
    HTTPServer(("0.0.0.0", HTML_PORT), Handler).serve_forever()


# =========================
# BITFINEX WS (L2 FIXED)
# =========================
async def ws_loop():
    global best_bid, best_ask

    async with websockets.connect(WS_URL, ping_interval=15) as ws:
        await ws.send(json.dumps({
            "event": "subscribe",
            "channel": "book",
            "symbol": SYMBOL,
            "prec": "P0",
            "len": BOOK_DEPTH
        }))

        while True:
            msg = json.loads(await ws.recv())

            if not isinstance(msg, list):
                continue

            payload = msg[1]

            # Snapshot
            if isinstance(payload, list) and payload and isinstance(payload[0], list):
                bid_levels.clear()
                ask_levels.clear()
                for p, c, a in payload:
                    if c == 0:
                        continue
                    if a > 0:
                        bid_levels[p] = a
                    else:
                        ask_levels[p] = abs(a)

            # Update
            elif isinstance(payload, list) and len(payload) == 3:
                p, c, a = payload
                if c == 0:
                    bid_levels.pop(p, None)
                    ask_levels.pop(p, None)
                else:
                    if a > 0:
                        bid_levels[p] = a
                        ask_levels.pop(p, None)
                    else:
                        ask_levels[p] = abs(a)
                        bid_levels.pop(p, None)

            if bid_levels and ask_levels:
                best_bid = max(bid_levels)
                best_ask = min(ask_levels)
                strategy_tick()
                generate_html()


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
