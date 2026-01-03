import asyncio
import json
import random
import time
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

BOOK_DEPTH = 25
ORDER_SIZE = 0.002
FEE = 0.0004

INITIAL_BALANCE = 10_000.0
MAX_INVENTORY = 0.05

IMBALANCE_THRESHOLD = 0.20
DELTA_THRESHOLD = 3.0
MIN_SPREAD_RATIO = 0.00005
SPOOF_RATIO = 0.6

FORCE_CLOSE_UPNL = 5.0

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

prev_bid_vol = 0.0
prev_ask_vol = 0.0
last_mid = None

trade_history = deque(maxlen=50)
html_content = ""

# =========================
# UTIL
# =========================
def mid_price():
    if bid_levels and ask_levels:
        return (max(bid_levels) + min(ask_levels)) / 2
    return 0.0

def orderbook_imbalance():
    bid_vol = sum(list(bid_levels.values())[:BOOK_DEPTH])
    ask_vol = sum(list(ask_levels.values())[:BOOK_DEPTH])
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)

def unrealized_pnl():
    return inventory * mid_price()

# =========================
# ADVERSE SELECTION FILTER
# =========================
def adverse_selection_filter(side):
    global prev_bid_vol, prev_ask_vol, last_mid

    mid = mid_price()
    if mid == 0:
        return False

    spread = min(ask_levels) - max(bid_levels)
    if spread / mid < MIN_SPREAD_RATIO:
        return False

    curr_bid_vol = sum(list(bid_levels.values())[:BOOK_DEPTH])
    curr_ask_vol = sum(list(ask_levels.values())[:BOOK_DEPTH])

    bid_delta = prev_bid_vol - curr_bid_vol
    ask_delta = prev_ask_vol - curr_ask_vol

    prev_bid_vol = curr_bid_vol
    prev_ask_vol = curr_ask_vol

    if side == "BUY" and ask_delta > DELTA_THRESHOLD:
        return False
    if side == "SELL" and bid_delta > DELTA_THRESHOLD:
        return False

    if last_mid is not None:
        if side == "BUY" and mid < last_mid:
            return False
        if side == "SELL" and mid > last_mid:
            return False

    last_mid = mid
    return True

# =========================
# EXECUTION
# =========================
def execute_trade(side):
    global balance, inventory, realized_pnl

    price = mid_price()
    if price == 0:
        return

    if side == "BUY":
        cost = price * ORDER_SIZE
        if balance < cost:
            return
        balance -= cost * (1 + FEE)
        inventory += ORDER_SIZE

    if side == "SELL":
        if inventory < ORDER_SIZE:
            return
        balance += price * ORDER_SIZE * (1 - FEE)
        inventory -= ORDER_SIZE

    trade_history.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "side": side,
        "price": price,
        "qty": ORDER_SIZE
    })

# =========================
# CORE LOGIC
# =========================
def strategy_step():
    imbalance = orderbook_imbalance()
    mid = mid_price()

    if unrealized_pnl() >= FORCE_CLOSE_UPNL and inventory > 0:
        execute_trade("SELL")
        return

    if imbalance > IMBALANCE_THRESHOLD:
        if adverse_selection_filter("BUY"):
            execute_trade("BUY")

    elif imbalance < -IMBALANCE_THRESHOLD:
        if adverse_selection_filter("SELL"):
            execute_trade("SELL")

# =========================
# HTML
# =========================
def generate_html():
    global html_content
    html_content = f"""
    <html><body style="background:#111;color:#0f0;font-family:monospace">
    <h2>RAMM-ASF — Bitfinex</h2>
    <p>Balance: {balance:.2f} USD</p>
    <p>Inventory: {inventory:.4f} BTC</p>
    <p>Realized PnL: {realized_pnl:.2f}</p>
    <p>Unrealized PnL: {unrealized_pnl():.2f}</p>
    <p>Mid: {mid_price():.2f}</p>
    <h3>Trades</h3>
    <ul>
    {''.join(f"<li>{t['time']} {t['side']} @ {t['price']:.2f}</li>" for t in trade_history)}
    </ul>
    </body></html>
    """

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(html_content.encode())

def start_http():
    print("🌐", ngrok.connect(5008))
    HTTPServer(("0.0.0.0", 5008), Handler).serve_forever()

# =========================
# BITFINEX WS
# =========================
async def ws_loop():
    global bid_levels, ask_levels

    while True:
        try:
            print("🔌 Connecting to Bitfinex WS...")

            async with websockets.connect(
                "wss://api-pub.bitfinex.com/ws/2",
                ping_interval=20,
                ping_timeout=20,
                max_queue=1000
            ) as ws:

                await ws.send(json.dumps({
                    "event": "subscribe",
                    "channel": "book",
                    "symbol": SYMBOL,
                    "prec": "P0",
                    "freq": "F0",
                    "len": BOOK_DEPTH
                }))

                print("✅ Subscribed to Bitfinex L2 book")

                async for msg in ws:
                    data = json.loads(msg)

                    # mensagens de controle
                    if not isinstance(data, list) or len(data) < 2:
                        continue

                    payload = data[1]

                    # heartbeat
                    if payload == "hb":
                        continue

                    # ================= SNAPSHOT =================
                    if isinstance(payload, list) and isinstance(payload[0], list):
                        for entry in payload:
                            if len(entry) != 3:
                                continue

                            price, count, amount = entry
                            price = float(price)
                            count = int(count)
                            amount = float(amount)

                            book = bid_levels if amount > 0 else ask_levels

                            if count == 0:
                                book.pop(price, None)
                            else:
                                book[price] = abs(amount)

                    # ================= UPDATE =================
                    elif isinstance(payload, list) and len(payload) == 3:
                        price, count, amount = payload
                        price = float(price)
                        count = int(count)
                        amount = float(amount)

                        book = bid_levels if amount > 0 else ask_levels

                        if count == 0:
                            book.pop(price, None)
                        else:
                            book[price] = abs(amount)

                    else:
                        continue

                    if bid_levels and ask_levels:
                        bid_levels = dict(sorted(bid_levels.items(), reverse=True))
                        ask_levels = dict(sorted(ask_levels.items()))

                        strategy_step()
                        generate_html()

        except Exception as e:
            print("⚠️ WS error:", e)
            await asyncio.sleep(3)


# =========================
# START
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http, daemon=True).start()
    asyncio.run(ws_loop())
