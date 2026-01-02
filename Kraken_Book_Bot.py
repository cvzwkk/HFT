import os
import asyncio
import json
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

INITIAL_BALANCE = 10000.0
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05
FEE = 0.0004
IMBALANCE_THRESHOLD = 0.2
TRADE_HISTORY_LIMIT = 25

# =========================
# AUTENTICA NGROK
# =========================
conf.get_default().auth_token = NGROK_AUTH_TOKEN

# =========================
# STATE GLOBAL
# =========================
balance = INITIAL_BALANCE
inventory = 0.0
realized_pnl = 0.0
trade_history = deque(maxlen=TRADE_HISTORY_LIMIT)

bid_levels = {}
ask_levels = {}
best_bid = 0.0
best_ask = 0.0
html_content = ""

# =========================
# UTILITIES
# =========================
def mid_price():
    return (best_bid + best_ask) / 2 if best_bid and best_ask else 0

def orderbook_imbalance():
    bid_vol = sum(v for _, v in list(bid_levels.items())[:BOOK_DEPTH])
    ask_vol = sum(v for _, v in list(ask_levels.items())[:BOOK_DEPTH])
    if bid_vol + ask_vol == 0:
        return 0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)

def execute_order(side, price, qty):
    global balance, inventory, realized_pnl, trade_history
    now = datetime.now(timezone.utc).isoformat()

    if side == "BUY":
        cost = qty * price
        fee = cost * FEE
        balance -= cost + fee
        inventory += qty
        trade_history.append({
            "side": "BUY",
            "entry_price": price,
            "exit_price": None,
            "qty": qty,
            "pnl": 0,
            "time": now
        })
    elif side == "SELL":
        proceeds = qty * price
        fee = proceeds * FEE
        balance += proceeds - fee
        inventory -= qty
        pnl = proceeds - fee
        realized_pnl += pnl

        # Atualiza último BUY
        for t in reversed(trade_history):
            if t["side"] == "BUY" and t["exit_price"] is None:
                t["exit_price"] = price
                t["pnl"] = pnl
                break

        trade_history.append({
            "side": "SELL",
            "entry_price": None,
            "exit_price": price,
            "qty": qty,
            "pnl": pnl,
            "time": now
        })

def check_book_and_trade():
    mid = mid_price()
    imbalance = orderbook_imbalance()
    if imbalance > IMBALANCE_THRESHOLD and inventory + ORDER_SIZE <= MAX_INVENTORY:
        execute_order("BUY", best_bid, ORDER_SIZE)
    if imbalance < -IMBALANCE_THRESHOLD and inventory - ORDER_SIZE >= -MAX_INVENTORY:
        execute_order("SELL", best_ask, ORDER_SIZE)

def generate_html():
    global html_content
    mid = mid_price()
    html = f"""
    <html>
    <head>
        <title>Kraken Book Paper Trading</title>
        <meta http-equiv="refresh" content="2">
        <style>
            body {{ font-family: monospace; background:#111; color:#0f0; padding:20px; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
            th, td {{ border: 1px solid #0f0; padding: 5px; text-align: center; }}
        </style>
    </head>
    <body>
        <h1>Kraken Book Paper Trading</h1>
        <p>Balance: {balance:.2f} USD | Inventory: {inventory:.4f} BTC | Realized PnL: {realized_pnl:.2f}</p>
        <p>Best Bid: {best_bid} | Best Ask: {best_ask} | Mid: {mid:.2f}</p>
        <h2>Trade History (Last {TRADE_HISTORY_LIMIT})</h2>
        <table>
            <tr><th>Time</th><th>Side</th><th>Entry Price</th><th>Exit Price</th><th>Qty</th><th>PnL</th></tr>
    """
    for t in list(trade_history):
        entry = f"{t['entry_price']:.2f}" if t['entry_price'] else "-"
        exit_ = f"{t['exit_price']:.2f}" if t['exit_price'] else "-"
        html += f"<tr><td>{t['time']}</td><td>{t['side']}</td><td>{entry}</td><td>{exit_}</td><td>{t['qty']:.4f}</td><td>{t['pnl']:.2f}</td></tr>"
    html += "</table></body></html>"
    html_content = html

# =========================
# HTTP SERVER
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))

def start_http_server():
    public_url = ngrok.connect(5008)
    print(f"HTTP server live at: {public_url}")
    server = HTTPServer(('0.0.0.0', 5008), SimpleHandler)
    server.serve_forever()

# =========================
# WEBSOCKET BOOK LOOP
# =========================
async def ws_loop():
    global bid_levels, ask_levels, best_bid, best_ask
    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        await ws.send(json.dumps({
            "method": "subscribe",
            "params": {"channel": "book", "symbol": [SYMBOL], "depth": BOOK_DEPTH}
        }))
        async for msg in ws:
            try:
                data = json.loads(msg)
                if data.get("channel") != "book":
                    continue
                book = data["data"][0]

                if "bids" in book:
                    for entry in book["bids"]:
                        price = float(entry["price"])
                        qty = float(entry["qty"])
                        if qty == 0:
                            bid_levels.pop(price, None)
                        else:
                            bid_levels[price] = qty
                if "asks" in book:
                    for entry in book["asks"]:
                        price = float(entry["price"])
                        qty = float(entry["qty"])
                        if qty == 0:
                            ask_levels.pop(price, None)
                        else:
                            ask_levels[price] = qty

                bid_levels = dict(sorted(bid_levels.items(), reverse=True))
                ask_levels = dict(sorted(ask_levels.items()))
                if not bid_levels or not ask_levels:
                    continue

                best_bid = next(iter(bid_levels))
                best_ask = next(iter(ask_levels))

                check_book_and_trade()
                generate_html()
            except Exception as e:
                print("WS error:", e)

# =========================
# START EVERYTHING
# =========================
if __name__ == "__main__":
    nest_asyncio.apply()
    Thread(target=start_http_server, daemon=True).start()
    asyncio.run(ws_loop())
