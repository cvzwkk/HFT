import asyncio
import json
import time
import random
from datetime import datetime, timezone
from collections import deque
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import websockets
import nest_asyncio
from pyngrok import ngrok, conf

# =========================
# CONFIG
# =========================
NGROK_AUTH_TOKEN = "37fJKKexs66q3bWBAAelBYiU2Yp_7Fq6yLN25TUj43fiBHfEN"

WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"
BOOK_LEN = 25

INITIAL_BALANCE = 100.0
ORDER_SIZE = 0.00004
MAX_INVENTORY = 0.00021
FEE = 0.0004

IMBALANCE_THRESHOLD = 0.2
TRAILING_PCT = 0.001     # 0.1%
FORCE_CLOSE_PNL = 5.0    # USD

MIN_SLIPPAGE = -0.0002
MAX_SLIPPAGE = 0.0002

TRADE_HISTORY_LIMIT = 25

# =========================
# NGROK
# =========================
conf.get_default().auth_token = NGROK_AUTH_TOKEN

# =========================
# STATE
# =========================
balance = INITIAL_BALANCE
inventory = 0.0
realized_pnl = 0.0

bid_levels = {}
ask_levels = {}

best_bid = 0.0
best_ask = 0.0
last_trade_price = None

trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)
total_trades = 0

trailing_stop = None
trailing_profit = None

html_content = ""

# =========================
# UTILS
# =========================
def mid_price():
    return (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0


def orderbook_imbalance(depth=10):
    bids = list(bid_levels.values())[:depth]
    asks = list(ask_levels.values())[:depth]
    if not bids or not asks:
        return 0.0
    b = sum(bids)
    a = sum(asks)
    return (b - a) / (b + a)


def fill_from_book(levels, qty, is_bid):
    remaining = qty
    fills = []

    prices = sorted(levels.keys(), reverse=is_bid)
    for price in prices:
        if remaining <= 0:
            break

        available = levels[price]
        take = min(available, remaining)

        slippage = random.uniform(MIN_SLIPPAGE, MAX_SLIPPAGE)
        exec_price = price * (1 + slippage)

        fills.append((exec_price, take))
        remaining -= take

    if remaining > 1e-9:
        return []

    return fills


def unrealized_pnl():
    if inventory <= 0 or last_trade_price is None:
        return 0.0
    return (best_bid - last_trade_price) * inventory

# =========================
# EXECUTION
# =========================
def execute_order(side, qty, reason=""):
    global balance, inventory, realized_pnl
    global trailing_stop, trailing_profit, total_trades, last_trade_price

    if qty <= 0:
        return

    now = datetime.now(timezone.utc).isoformat()

    if side == "BUY":
        if not ask_levels:
            return
        if balance < best_ask * qty:
            return

        fills = fill_from_book(ask_levels, qty, is_bid=False)
        if not fills:
            return

        cost = sum(p * q for p, q in fills)
        fee = cost * FEE

        balance -= (cost + fee)
        inventory += qty

        entry = cost / qty
        last_trade_price = entry

        trailing_stop = entry * (1 - TRAILING_PCT)
        trailing_profit = entry * (1 + TRAILING_PCT)

        trade_history.append({
            "side": "BUY",
            "entry": entry,
            "exit": None,
            "qty": qty,
            "pnl": 0.0,
            "time": now
        })

        total_trades += 1

    elif side == "SELL":
        if inventory < qty or not bid_levels:
            return

        fills = fill_from_book(bid_levels, qty, is_bid=True)
        if not fills:
            return

        proceeds = sum(p * q for p, q in fills)
        fee = proceeds * FEE

        balance += (proceeds - fee)
        inventory -= qty

        exit_price = proceeds / qty
        last_trade_price = None

        pnl = 0.0
        for t in reversed(trade_history):
            if t["side"] == "BUY" and t["exit"] is None:
                pnl = (exit_price - t["entry"]) * qty
                t["exit"] = exit_price
                t["pnl"] = pnl
                realized_pnl += pnl
                break

        trade_history.append({
            "side": "SELL",
            "entry": None,
            "exit": exit_price,
            "qty": qty,
            "pnl": pnl,
            "time": now,
            "reason": reason
        })

        trailing_stop = None
        trailing_profit = None
        total_trades += 1

# =========================
# STRATEGY
# =========================
def strategy():
    if not bid_levels or not ask_levels:
        return

    imb = orderbook_imbalance()

    if (
        imb > IMBALANCE_THRESHOLD
        and inventory + ORDER_SIZE <= MAX_INVENTORY
        and balance >= best_ask * ORDER_SIZE
    ):
        execute_order("BUY", ORDER_SIZE, "imbalance")

    elif (
        imb < -IMBALANCE_THRESHOLD
        and inventory >= ORDER_SIZE
    ):
        execute_order("SELL", ORDER_SIZE, "imbalance")

    # Trailing
    if inventory > 0 and last_trade_price:
        if best_bid <= trailing_stop or best_bid >= trailing_profit:
            execute_order("SELL", ORDER_SIZE, "trailing")

    # FORCE CLOSE PROFIT
    if inventory > 0 and unrealized_pnl() >= FORCE_CLOSE_PNL:
        execute_order("SELL", inventory, "force_profit")

# =========================
# HTML
# =========================
def generate_html():
    global html_content
    u = unrealized_pnl()

    rows = ""
    for t in trade_history:
        rows += f"""
        <tr>
            <td>{t['time']}</td>
            <td>{t['side']}</td>
            <td>{t.get('entry','-')}</td>
            <td>{t.get('exit','-')}</td>
            <td>{t['qty']}</td>
            <td>{t['pnl']:.2f}</td>
        </tr>
        """

    html_content = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="2">
        <style>
            body {{ background:#111; color:#0f0; font-family: monospace; }}
            table {{ width:100%; border-collapse:collapse; }}
            td, th {{ border:1px solid #0f0; padding:4px; }}
        </style>
    </head>
    <body>
        <h2>Bitfinex WS L2 – Paper MM</h2>
        <p>Balance: {balance:.2f} USD</p>
        <p>Inventory: {inventory:.4f} BTC</p>
        <p>Realized PnL: {realized_pnl:.2f}</p>
        <p>Unrealized PnL: {u:.2f}</p>
        <p>Best Bid: {best_bid} | Best Ask: {best_ask}</p>
        <p>Total Trades: {total_trades}</p>

        <table>
            <tr>
                <th>Time</th><th>Side</th><th>Entry</th>
                <th>Exit</th><th>Qty</th><th>PnL</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

# =========================
# HTTP SERVER
# =========================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html_content.encode())

def start_http():
    public_url = ngrok.connect(5008)
    print("🌍 Public URL:", public_url)
    HTTPServer(("0.0.0.0", 5008), Handler).serve_forever()

# =========================
# WS LOOP
# =========================
async def ws_loop():
    global bid_levels, ask_levels, best_bid, best_ask

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=15) as ws:
                await ws.send(json.dumps({
                    "event": "subscribe",
                    "channel": "book",
                    "symbol": SYMBOL,
                    "len": BOOK_LEN
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if isinstance(data, dict):
                        continue

                    payload = data[1]

                    if isinstance(payload, list) and isinstance(payload[0], list):
                        bid_levels.clear()
                        ask_levels.clear()
                        for p, c, a in payload:
                            if a > 0:
                                bid_levels[p] = a
                            else:
                                ask_levels[p] = abs(a)

                    elif isinstance(payload, list):
                        p, c, a = payload
                        if c == 0:
                            if a > 0:
                                bid_levels.pop(p, None)
                            else:
                                ask_levels.pop(p, None)
                        else:
                            if a > 0:
                                bid_levels[p] = a
                            else:
                                ask_levels[p] = abs(a)

                    if not bid_levels or not ask_levels:
                        continue

                    bid_levels = dict(sorted(bid_levels.items(), reverse=True))
                    ask_levels = dict(sorted(ask_levels.items()))

                    best_bid = next(iter(bid_levels))
                    best_ask = next(iter(ask_levels))

                    strategy()
                    generate_html()

        except Exception as e:
            print("WS error:", e)
            await asyncio.sleep(3)

# =========================
# START
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
