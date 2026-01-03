import asyncio
import json
import math
import os
import time
from collections import deque
from datetime import datetime

import websockets

# =========================
# CONFIG
# =========================
WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"

BOOK_DEPTH = 25
ORDER_SIZE = 0.002
MAX_INVENTORY = 0.05

INITIAL_BALANCE = 10_000.0
FEE = 0.0004

RISK_GAMMA = 0.15
TIME_HORIZON = 1.0
UNREALIZED_CLOSE_PNL = 5.0

TOXIC_THRESHOLD = 0.65
ADVERSE_THRESHOLD = 0.6

REFRESH_RATE = 0.3  # seconds

# =========================
# STATE
# =========================
balance = INITIAL_BALANCE
inventory = 0.0
realized_pnl = 0.0

bids = {}
asks = {}

trade_history = deque(maxlen=25)
mid_prices = deque(maxlen=200)

toxic_score = 0.0
adverse_score = 0.0
last_mid = 0.0

# =========================
# UTILS
# =========================
def clear():
    os.system("cls" if os.name == "nt" else "clear")

def mid_price():
    if not bids or not asks:
        return 0.0
    return (max(bids) + min(asks)) / 2

def volatility():
    if len(mid_prices) < 10:
        return 0.0
    m = sum(mid_prices) / len(mid_prices)
    return math.sqrt(sum((p - m) ** 2 for p in mid_prices) / len(mid_prices))

def unrealized_pnl():
    return inventory * mid_price()

def book_imbalance():
    bv = sum(bids.values())
    av = sum(asks.values())
    if bv + av == 0:
        return 0.0
    return (bv - av) / (bv + av)

# =========================
# AVELLANEDA–STOIKOV
# =========================
def reservation_price():
    return mid_price() - RISK_GAMMA * inventory

def optimal_spread():
    sigma = volatility()
    if sigma == 0:
        return 0.5
    return RISK_GAMMA * sigma**2 * TIME_HORIZON + 2 * math.log(1 + RISK_GAMMA)

# =========================
# EXECUTION (QUEUE-AWARE)
# =========================
def fill_from_book(levels, qty, reverse):
    fills = []
    for price in sorted(levels.keys(), reverse=reverse):
        avail = levels[price]
        f = min(avail, qty)
        fills.append((price, f))
        levels[price] -= f
        if levels[price] <= 0:
            del levels[price]
        qty -= f
        if qty <= 0:
            break
    return fills

def execute(side, qty):
    global balance, inventory, realized_pnl

    if qty <= 0:
        return

    if side == "BUY":
        if balance <= 0 or not asks:
            return
        fills = fill_from_book(asks, qty, False)
        cost = sum(p*q for p,q in fills)
        balance -= cost * (1 + FEE)
        inventory += qty
        trade_history.append(("BUY", cost/qty, qty))

    elif side == "SELL":
        if inventory <= 0 or not bids:
            return
        fills = fill_from_book(bids, qty, True)
        proceeds = sum(p*q for p,q in fills)
        balance += proceeds * (1 - FEE)
        inventory -= qty

        for t in reversed(trade_history):
            if t[0] == "BUY":
                pnl = (proceeds/qty - t[1]) * qty
                realized_pnl += pnl
                break

# =========================
# STRATEGY CORE
# =========================
def strategy():
    global last_mid, toxic_score, adverse_score

    mid = mid_price()
    if mid == 0:
        return

    mid_prices.append(mid)
    last_mid = mid

    # Force close unrealized
    if unrealized_pnl() >= UNREALIZED_CLOSE_PNL:
        execute("SELL", abs(inventory))
        return

    imb = book_imbalance()
    adverse_score = adverse_score * 0.85 + abs(imb) * 0.15

    if adverse_score > ADVERSE_THRESHOLD or toxic_score > TOXIC_THRESHOLD:
        return

    r = reservation_price()
    spread = optimal_spread()

    bid_q = r - spread/2
    ask_q = r + spread/2

    if bids and bid_q >= max(bids) and inventory + ORDER_SIZE <= MAX_INVENTORY:
        execute("BUY", ORDER_SIZE)

    if asks and ask_q <= min(asks) and inventory - ORDER_SIZE >= -MAX_INVENTORY:
        execute("SELL", ORDER_SIZE)

# =========================
# TUI LOOP
# =========================
async def tui_loop():
    while True:
        clear()
        print("🛡️  AEGIS-MM  | Bitfinex WS L2\n")
        print(f"Mid Price      : {last_mid:,.2f}")
        print(f"Balance (USD)  : {balance:,.2f}")
        print(f"Inventory BTC : {inventory:.4f}")
        print(f"Unrealized PnL: {unrealized_pnl():,.2f}")
        print(f"Realized PnL  : {realized_pnl:,.2f}\n")

        print(f"Volatility    : {volatility():.4f}")
        print(f"Book Imb.     : {book_imbalance():.2f}")
        print(f"Adverse Score : {adverse_score:.2f}")
        print(f"Toxic Score   : {toxic_score:.2f}\n")

        print("Last Trades:")
        for t in list(trade_history)[-5:]:
            print(f" {t[0]:4} | price {t[1]:,.2f} | qty {t[2]:.4f}")

        await asyncio.sleep(REFRESH_RATE)

# =========================
# WS LOOP
# =========================
async def ws_loop():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "event": "subscribe",
            "channel": "book",
            "symbol": SYMBOL,
            "prec": "P0",
            "freq": "F0",
            "len": BOOK_DEPTH
        }))

        while True:
            msg = json.loads(await ws.recv())
            if not isinstance(msg, list) or msg[1] == "hb":
                continue

            data = msg[1]
            updates = data if isinstance(data[0], list) else [data]

            for price, count, amount in updates:
                if count == 0:
                    bids.pop(price, None)
                    asks.pop(price, None)
                elif amount > 0:
                    bids[price] = amount
                else:
                    asks[price] = abs(amount)

            strategy()

# =========================
# MAIN
# =========================
async def main():
    await asyncio.gather(ws_loop(), tui_loop())

if __name__ == "__main__":
    print("🛡️ AEGIS-MM TUI started")
    asyncio.run(main())
