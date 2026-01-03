import asyncio
import json
import math
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

INITIAL_BALANCE = 10000.0
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05
FEE = 0.0004

TRADE_HISTORY_LIMIT = 50

trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)
trade_id = 0

# Avellaneda–Stoikov
GAMMA = 0.05
K = 1.2
TIME_HORIZON = 30.0

SIGMA_WINDOW = 200
FORCE_CLOSE_PNL = 5.0


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

mid_prices = deque(maxlen=SIGMA_WINDOW)
html_content = ""

# =========================
# UTILS
# =========================
def mid_price():
    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    return 0.0

def calc_sigma2():
    if len(mid_prices) < 2:
        return 0.0
    rets = [
        math.log(mid_prices[i] / mid_prices[i - 1])
        for i in range(1, len(mid_prices))
        if mid_prices[i - 1] > 0
    ]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    return sum((r - mean) ** 2 for r in rets) / len(rets)

def unrealized_pnl():
    return inventory * mid_price()

def fill_from_book(levels, qty, is_bid):
    fills = []
    prices = sorted(levels.keys(), reverse=is_bid)
    for p in prices:
        if qty <= 0:
            break
        avail = levels[p]
        take = min(avail, qty)
        fills.append((p, take))
        levels[p] -= take
        if levels[p] <= 1e-9:
            del levels[p]
        qty -= take
    return fills

# =========================
# EXECUTION
# =========================
def execute(side, qty):
    global balance, inventory, realized_pnl, trade_id

    if qty <= 0:
        return

    price = mid_price()
    if price == 0:
        return

    trade_id += 1
    now = datetime.now(timezone.utc).isoformat()

    if side == "BUY":
        if balance < price * qty:
            return

        balance -= price * qty * (1 + FEE)
        inventory += qty

        trade_history.append({
            "id": trade_id,
            "side": "BUY",
            "price": price,
            "qty": qty,
            "time": now,
            "status": "OPEN",
            "pnl": 0.0
        })

    elif side == "SELL":
        if inventory < qty:
            return

        balance += price * qty * (1 - FEE)
        inventory -= qty

        # Fecha posições BUY abertas (FIFO)
        for t in trade_history:
            if t["side"] == "BUY" and t["status"] == "OPEN":
                pnl = (price - t["price"]) * t["qty"]
                t["status"] = "CLOSED"
                t["exit_price"] = price
                t["pnl"] = pnl
                realized_pnl += pnl
                break

        trade_history.append({
            "id": trade_id,
            "side": "SELL",
            "price": price,
            "qty": qty,
            "time": now,
            "status": "CLOSED",
            "pnl": 0.0
        })



# =========================
# AVELLANEDA–STOIKOV
# =========================
def avellaneda_stoikov():
    S = mid_price()
    if S == 0:
        return

    sigma2 = calc_sigma2()
    r = S - inventory * GAMMA * sigma2 * TIME_HORIZON
    spread = (GAMMA * sigma2 * TIME_HORIZON) + (2 / GAMMA) * math.log(1 + GAMMA / K)

    bid = r - spread / 2
    ask = r + spread / 2

    if bid > best_bid and inventory + ORDER_SIZE <= MAX_INVENTORY:
        execute("BUY", ORDER_SIZE)

    if ask < best_ask and inventory - ORDER_SIZE >= -MAX_INVENTORY:
        execute("SELL", ORDER_SIZE)

# =========================
# RISK
# =========================
def risk_control():
    upnl = unrealized_pnl()
    if upnl >= FORCE_CLOSE_PNL and inventory > 0:
        execute("SELL", inventory)


# =========================
# HTML
# =========================
def generate_html():
    global html_content

    rows = ""
    for t in reversed(trade_history):
        rows += f"""
        <tr>
            <td>{t["time"]}</td>
            <td>{t["side"]}</td>
            <td>{t["price"]:.2f}</td>
            <td>{t["qty"]:.4f}</td>
            <td>{t["status"]}</td>
            <td>{t["pnl"]:.2f}</td>
        </tr>
        """

    html_content = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="2">
        <style>
            body {{ background:#111;color:#0f0;font-family:monospace }}
            table {{ border-collapse:collapse;width:100% }}
            th,td {{ border:1px solid #0f0;padding:4px;text-align:center }}
        </style>
    </head>
    <body>
        <h2>AS-RAMM-L2 — Avellaneda–Stoikov MM</h2>

        <p>Balance: {balance:.2f} USD</p>
        <p>Inventory: {inventory:.4f} BTC</p>
        <p>Realized PnL: {realized_pnl:.2f}</p>
        <p>Unrealized PnL: {unrealized_pnl():.2f}</p>
        <p>Best Bid: {best_bid} | Best Ask: {best_ask}</p>

        <h3>Trade History (last {TRADE_HISTORY_LIMIT})</h3>
        <table>
            <tr>
                <th>Time</th>
                <th>Side</th>
                <th>Price</th>
                <th>Qty</th>
                <th>Status</th>
                <th>PnL</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(html_content.encode())

def start_http():
    ngrok.connect(5010)
    HTTPServer(("0.0.0.0", 5010), Handler).serve_forever()

# =========================
# WEBSOCKET (FIXED)
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
            "len": 25
        }))

        async for msg in ws:
            data = json.loads(msg)

            if not isinstance(data, list) or len(data) < 2:
                continue

            payload = data[1]

            # heartbeat
            if payload == "hb":
                continue

            # snapshot
            if isinstance(payload, list) and isinstance(payload[0], list):
                bid_levels.clear()
                ask_levels.clear()
                for p, c, q in payload:
                    if q > 0:
                        bid_levels[p] = q
                    elif q < 0:
                        ask_levels[p] = -q
            # delta
            elif isinstance(payload, list) and len(payload) == 3:
                p, c, q = payload
                if q > 0:
                    bid_levels[p] = q
                elif q < 0:
                    ask_levels[p] = -q
                else:
                    bid_levels.pop(p, None)
                    ask_levels.pop(p, None)

            if bid_levels and ask_levels:
                best_bid = max(bid_levels)
                best_ask = min(ask_levels)
                mid_prices.append(mid_price())

                avellaneda_stoikov()
                risk_control()
                generate_html()

# =========================
# START
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    conf.get_default().auth_token = NGROK_AUTH_TOKEN
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
