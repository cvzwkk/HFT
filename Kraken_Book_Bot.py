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

WS_URL = "wss://ws.kraken.com/v2"
SYMBOL = "BTC/USD"
BOOK_DEPTH = 10

INITIAL_BALANCE = 10_000.0
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05
FEE = 0.0004

IMBALANCE_THRESHOLD = 0.2
TRADE_HISTORY_LIMIT = 25
TRAILING_PERCENT = 0.001  # 0.1%

MIN_LATENCY = 0.05
MAX_LATENCY = 0.2

MIN_SLIPPAGE = -0.0002
MAX_SLIPPAGE = 0.0002

# =========================
# NGROK AUTH
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

trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)
total_trades = 0

trailing_stop = None
trailing_profit = None

html_content = ""

# =========================
# UTILS
# =========================
def mid_price():
    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    return 0.0


def orderbook_imbalance():
    bid_vol = sum(list(bid_levels.values())[:BOOK_DEPTH])
    ask_vol = sum(list(ask_levels.values())[:BOOK_DEPTH])
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)


def fill_from_book(levels, qty, is_bid):
    fills = []
    remaining = qty

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

    if remaining > 1e-8:
        return []

    return fills


# =========================
# EXECUTION
# =========================
def execute_order(side, qty):
    global balance, inventory, realized_pnl
    global trailing_stop, trailing_profit, total_trades

    if qty <= 0:
        return

    now = datetime.now(timezone.utc).isoformat()

    time.sleep(random.uniform(MIN_LATENCY, MAX_LATENCY))

    if side == "BUY":
        if not ask_levels:
            return

        cost_estimate = best_ask * qty
        if balance < cost_estimate:
            return

        fills = fill_from_book(ask_levels, qty, is_bid=False)
        if not fills:
            return

        total_cost = sum(p * q for p, q in fills)
        fee = total_cost * FEE

        balance -= (total_cost + fee)
        inventory += qty

        entry_price = total_cost / qty
        trailing_stop = entry_price * (1 - TRAILING_PERCENT)
        trailing_profit = entry_price * (1 + TRAILING_PERCENT)

        trade_history.append({
            "side": "BUY",
            "entry": entry_price,
            "exit": None,
            "qty": qty,
            "pnl": 0.0,
            "time": now
        })

        total_trades += 1

    elif side == "SELL":
        if not bid_levels or inventory < qty:
            return

        fills = fill_from_book(bid_levels, qty, is_bid=True)
        if not fills:
            return

        total_proceeds = sum(p * q for p, q in fills)
        fee = total_proceeds * FEE

        balance += (total_proceeds - fee)
        inventory -= qty

        exit_price = total_proceeds / qty

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
            "time": now
        })

        trailing_stop = None
        trailing_profit = None
        total_trades += 1


# =========================
# STRATEGY (BOOK ONLY)
# =========================
def check_trailing(price):
    global trailing_stop, trailing_profit

    if inventory <= 0:
        return

    if trailing_stop and price <= trailing_stop:
        execute_order("SELL", ORDER_SIZE)

    elif trailing_profit and price >= trailing_profit:
        execute_order("SELL", ORDER_SIZE)


def strategy():
    if not bid_levels or not ask_levels:
        return

    imbalance = orderbook_imbalance()
    mid = mid_price()

    # BUY pressure
    if (
        imbalance > IMBALANCE_THRESHOLD
        and inventory + ORDER_SIZE <= MAX_INVENTORY
        and balance >= best_ask * ORDER_SIZE
    ):
        execute_order("BUY", ORDER_SIZE)

    # SELL pressure
    elif (
        imbalance < -IMBALANCE_THRESHOLD
        and inventory >= ORDER_SIZE
    ):
        execute_order("SELL", ORDER_SIZE)

    check_trailing(mid)


# =========================
# HTML
# =========================
def generate_html():
    global html_content

    unrealized = inventory * mid_price()

    rows = ""
    for t in trade_history:
        rows += f"""
        <tr>
            <td>{t['time']}</td>
            <td>{t['side']}</td>
            <td>{t['entry'] or '-'}</td>
            <td>{t['exit'] or '-'}</td>
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
            td, th {{ border:1px solid #0f0; padding:5px; }}
        </style>
    </head>
    <body>
        <h2>Kraken L2 Market Making (Paper)</h2>
        <p>Balance: {balance:.2f} USD</p>
        <p>Inventory: {inventory:.4f} BTC</p>
        <p>Realized PnL: {realized_pnl:.2f}</p>
        <p>Unrealized PnL: {unrealized:.2f}</p>
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
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {
                        "channel": "book",
                        "symbol": [SYMBOL],
                        "depth": BOOK_DEPTH
                    }
                }))

                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("channel") != "book":
                        continue

                    book = data["data"][0]

                    for b in book.get("bids", []):
                        bid_levels[float(b["price"])] = float(b["qty"])

                    for a in book.get("asks", []):
                        ask_levels[float(a["price"])] = float(a["qty"])

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
# MAIN
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
