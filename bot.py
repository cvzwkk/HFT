import asyncio, aiohttp, numpy as np, os, json, uvicorn, nest_asyncio, sys
from collections import deque
from datetime import datetime
from fastapi import FastAPI
from pyngrok import ngrok
from numba import njit

nest_asyncio.apply()

# =========================
# CONFIG & MODELS
# =========================
NGROK_AUTHTOKEN = "36xhpiAn5cRi9ObeqeKYdJBZ13k_3z1GytiAf4Sn3czxWwNBm"
ORDERBOOK_APIS = {"Bitfinex": "https://api.bitfinex.com/v1/book/btcusd"}

@njit(fastmath=True)
def hma_core(prices, period):
    length = prices.shape[0]
    n = min(length, period)
    weights = np.arange(1, n + 1).astype(np.float64)
    wma = np.dot(prices[-n:], weights) / weights.sum()
    
    half_n = max(2, n // 2)
    h_weights = np.arange(1, half_n + 1).astype(np.float64)
    h_wma = np.dot(prices[-half_n:], h_weights) / h_weights.sum()
    
    return (2.0 * h_wma) - wma

# =========================
# ENGINE
# =========================
class PaperTrader:
    def __init__(self, balance=15000000):
        self.initial_balance = self.balance = balance
        self.positions = {e: None for e in ORDERBOOK_APIS}
        self.pnl = {e: 0.0 for e in ORDERBOOK_APIS}
        self.history = deque(maxlen=20)
        self.entry_size, self.add_ratio = 0.00039370, 2.5
        self.step, self.tp = 0.0018, 0.0005

    def trade(self, ex, side, price):
        pos = self.positions[ex]
        if not pos:
            self.positions[ex] = {"side": side, "avg": price, "btc": self.entry_size, "adds": 0,
                                 "tp": price * (1 + (self.tp if side == "buy" else -self.tp)),
                                 "next": price * (1 + (-self.step if side == "buy" else self.step))}
            self.history.append(f"[{datetime.now().strftime('%H:%M')}] OPEN {side.upper()} @ {price}")
        else:
            if pos["adds"] < 9:
                new_btc = pos["btc"] * self.add_ratio
                pos["avg"] = (pos["avg"] * pos["btc"] + price * new_btc) / (pos["btc"] + new_btc)
                pos["btc"] += new_btc
                pos["adds"] += 1
                pos["tp"] = pos["avg"] * (1 + (self.tp if side == "buy" else -self.tp))
                pos["next"] = price * (1 + (-self.step if side == "buy" else self.step))

    def close(self, ex, price, reason):
        pos = self.positions[ex]
        if not pos: return
        p = (price - pos["avg"]) * pos["btc"] if pos["side"] == "buy" else (pos["avg"] - price) * pos["btc"]
        self.balance += p
        self.pnl[ex] += p
        self.history.append(f"[{datetime.now().strftime('%H:%M')}] CLOSE {reason} PnL: {p:.2f}")
        self.positions[ex] = None

# =========================
# CORE LOOPS
# =========================
trader = PaperTrader()
price_history = {e: deque(maxlen=60) for e in ORDERBOOK_APIS}
public_url = "Connecting..."

async def console_monitor():
    """Static Dashboard using ANSI Escape Codes"""
    while True:
        total_pnl = sum(trader.pnl.values())
        # \033[H moves cursor to top, \033[J clears screen from cursor down
        sys.stdout.write("\033[H\033[J") 
        dashboard = [
            "================================================",
            "          TRADING BOT LIVE DASHBOARD            ",
            f"          URL: {public_url}",
            "================================================",
            f" Time:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f" Balance:  ${trader.balance:,.2f}",
            f" Total PnL: ${total_pnl:,.4f}",
            "------------------------------------------------",
            " ACTIVE POSITIONS:",
        ]
        for ex, pos in trader.positions.items():
            status = f" {ex}: {pos['side'].upper()} | Adds: {pos['adds']} | BTC: {pos['btc']:.6f}" if pos else f" {ex}: IDLE"
            dashboard.append(status)
        
        dashboard.append("------------------------------------------------")
        dashboard.append(" RECENT LOGS:")
        dashboard.extend(list(trader.history)[-5:])
        dashboard.append("================================================")
        
        sys.stdout.write("\n".join(dashboard) + "\n")
        sys.stdout.flush()
        await asyncio.sleep(1)

async def update_prices():
    async with aiohttp.ClientSession() as session:
        while True:
            for ex, url in ORDERBOOK_APIS.items():
                try:
                    async with session.get(url, timeout=2) as r:
                        data = await r.json()
                        price = float(data["asks"][0]["price"])
                        price_history[ex].append(price)
                        
                        if len(price_history[ex]) >= 12:
                            np_prices = np.array(price_history[ex], dtype=np.float64)
                            p1, p2 = hma_core(np_prices, 58), hma_core(np_prices, 10)
                            pos = trader.positions[ex]
                            
                            if not pos:
                                if p1 > price > min(p1, p2): trader.trade(ex, "buy", price)
                                elif p1 < price < max(p1, p2): trader.trade(ex, "sell", price)
                            else:
                                if (pos["side"] == "buy" and price >= pos["tp"]) or (pos["side"] == "sell" and price <= pos["tp"]):
                                    trader.close(ex, price, "TP")
                                elif (pos["side"] == "buy" and price <= pos["next"]) or (pos["side"] == "sell" and price >= pos["next"]):
                                    trader.trade(ex, pos["side"], price)
                except: continue
            await asyncio.sleep(1)

# =========================
# START
# =========================
app = FastAPI()

async def main():
    global public_url
    ngrok.set_auth_token(NGROK_AUTHTOKEN)
    try:
        tunnel = ngrok.connect(8000)
        public_url = tunnel.public_url
    except: public_url = "Error connecting Ngrok"

    asyncio.create_task(update_prices())
    asyncio.create_task(console_monitor())
    
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="critical")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
