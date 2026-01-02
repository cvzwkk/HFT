import os
import asyncio
import json
import random
from datetime import datetime, timezone
from collections import deque
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import websockets
from pyngrok import ngrok, conf
import nest_asyncio
import time

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
IGNORE_INITIAL_TRADES = 24
TRAILING_PERCENT = 0.1 / 100  # 0.1%

MIN_SLIPPAGE = -0.0005
MAX_SLIPPAGE = 0.0005

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
total_trades = 0
processed_trades = 0

bid_levels = {}  # {price: qty}
ask_levels = {}
best_bid = 0.0
best_ask = 0.0
html_content = ""

# Controle de trailing stop/profit
trailing_stop = None
trailing_profit = None

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

def fill_from_book(levels, qty_needed, is_bid=True):
    """
    Executa fills reais do book com slippage e partial fills
    """
    fills = []
    sorted_prices = sorted(levels.keys(), reverse=is_bid)
    for price in sorted_prices:
        available = levels[price]
        fill_qty = min(qty_needed, available)
        # aplica slippage aleatório
        slippage = random.uniform(MIN_SLIPPAGE, MAX_SLIPPAGE)
        fill_price = price * (1 + slippage)
        fills.append((fill_price, fill_qty))
        levels[price] -= fill_qty
        if levels[price] <= 1e-12:
            del levels[price]
        qty_needed -= fill_qty
        if qty_needed <= 0:
            break
    return fills

def calculate_unrealized_pnl():
    global inventory
    mid = mid_price()
    return (inventory * mid) if inventory else 0

def execute_order_real(side, qty):
    global balance, inventory, realized_pnl, trade_history, total_trades, processed_trades, trailing_stop, trailing_profit
    now = datetime.now(timezone.utc).isoformat()
    total_trades += 1

    if processed_trades < IGNORE_INITIAL_TRADES:
        processed_trades += 1
        return

    # Adiciona latência aleatória
    time.sleep(random.uniform(0.05, 0.2))  # 50-200ms

    fills = []
    if side == "BUY":
        if not ask_levels or qty <= 0:
            return
        fills = fill_from_book(ask_levels, qty, is_bid=False)
        total_cost = sum(p*q for p,q in fills)
        total_fee = total_cost * FEE
        balance -= total_cost + total_fee
        inventory += qty
        trailing_stop = min(p for p,_ in fills) * (1 - TRAILING_PERCENT)
        trailing_profit = max(p for p,_ in fills) * (1 + TRAILING_PERCENT)
        trade = {
            "side": "BUY",
            "entry_price": sum(p*q for p,q in fills)/qty,
            "exit_price": None,
            "qty": qty,
            "pnl": 0,
            "time": now
        }
        trade_history.append(trade)

    elif side == "SELL":
        if not bid_levels or qty <= 0:
            return
        fills = fill_from_book(bid_levels, qty, is_bid=True)
        total_proceeds = sum(p*q for p,q in fills)
        total_fee = total_proceeds * FEE
        balance += total_proceeds - total_fee
        inventory -= qty
        trailing_stop = max(p for p,_ in fills) * (1 + TRAILING_PERCENT)
        trailing_profit = min(p for p,_ in fills) * (1 - TRAILING_PERCENT)

        pnl = 0
        # Atualiza último BUY para exit_price real
        for t in reversed(trade_history):
            if t["side"] == "BUY" and t["exit_price"] is None:
                exit_price = sum(p*q for p,q in fills)/qty
                pnl = (exit_price - t["entry_price"]) * qty
                t["exit_price"] = exit_price
                t["pnl"] = pnl
                realized_pnl += pnl
                break

        trade = {
            "side": "SELL",
            "entry_price": None,
            "exit_price": sum(p*q for p,q in fills)/qty,
            "qty": qty,
            "pnl": pnl,
            "time": now
        }
        trade_history.append(trade)

def check_trailing(price):
    global trailing_stop, trailing_profit
    if trailing_stop and price <= trailing_stop:
        execute_order_real("SELL", ORDER_SIZE)
        trailing_stop = None
        trailing_profit = None
    if trailing_profit and price >= trailing_profit:
        execute_order_real("SELL", ORDER_SIZE)
        trailing_stop = None
        trailing_profit = None

def check_book_and_trade():
    imbalance = orderbook_imbalance()
    mid = mid_price()
    # filtro de picos/extremos do book
    if imbalance > IMBALANCE_THRESHOLD and inventory + ORDER_SIZE <= MAX_INVENTORY and best_ask <= mid * 1.001:
        execute_order_real("BUY", ORDER_SIZE)
    if imbalance < -IMBALANCE_THRESHOLD and inventory - ORDER_SIZE >= -MAX_INVENTORY and best_bid >= mid * 0.999:
        execute_order_real("SELL", ORDER_SIZE)
    check_trailing(mid)

def generate_html():
    global html_content
    mid = mid_price()
    unrealized_pnl = calculate_unrealized_pnl()
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
        <p>Balance: {balance:.2f} USD | Inventory: {inventory:.4f} BTC | Realized PnL: {realized_pnl:.2f} | Unrealized PnL: {unrealized_pnl:.2f}</p>
        <p>Best Bid: {best_bid} | Best Ask: {best_ask} | Mid: {mid:.2f} | Total Trades: {total_trades}</p>
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
