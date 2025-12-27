#add numba function to the models of prediction


#!pip install pyngrok

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================
# IMPORTS
# =========================
import asyncio
import aiohttp
import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pyngrok import ngrok
import uvicorn
import os
import json
import logging
from numba import njit


import nest_asyncio
nest_asyncio.apply()

# =========================
# NGROK
# =========================
NGROK_AUTHTOKEN = "36xhpiAn5cRi9ObeqeKYdJBZ13k_3z1GytiAf4Sn3czxWwNBm"
ngrok.set_auth_token(NGROK_AUTHTOKEN)

# =========================
# EXCHANGES & CONSTANTS
# =========================
ORDERBOOK_APIS = {
    "Bitfinex": "https://api.bitfinex.com/v1/book/btcusd"
}

STATE_FILE = "trader_state.json"

# =========================
# UTILS
# =========================
def safe_return(v):
    if v is None or np.isnan(v) or np.isinf(v):
        return None
    return float(v)

def log_returns(prices):
    return np.diff(np.log(prices + 1e-8))

# =========================
# SPREAD FUNCTION (ONLY EDIT)
# =========================
def micro_price(bid, ask, bid_sz, ask_sz):
    # Real Bitfinex taker execution (simulate 1 real trade crossing spread)
    return ask

@njit(fastmath=True)
def wma_numba(arr, n):
    n = min(arr.shape[0], n)
    weights_sum = n * (n + 1) * 0.5
    acc = 0.0
    w = 1.0
    for i in range(arr.shape[0] - n, arr.shape[0]):
        acc += arr[i] * w
        w += 1.0
    return acc / weights_sum


@njit(fastmath=True)
def slope_numba(arr, n):
    # simple linear regression slope
    n = min(n, arr.shape[0] - 1)
    x_mean = n * 0.5
    y_mean = 0.0
    for i in range(n + 1):
        y_mean += arr[arr.shape[0] - 1 - i]
    y_mean /= (n + 1)

    num = 0.0
    den = 0.0
    for i in range(n + 1):
        x = i - x_mean
        y = arr[arr.shape[0] - 1 - i] - y_mean
        num += x * y
        den += x * x

    return num / (den + 1e-12)


@njit(fastmath=True)
def momentum_numba(returns):
    s = 0.0
    decay = 1.0
    for i in range(returns.shape[0] - 1, -1, -1):
        s += returns[i] * decay
        decay *= 0.95
    return s

@njit(fastmath=True)
def hma_core(prices, period):
    length = prices.shape[0]

    half = max(2, period // 2)
    half = min(half, length)
    period = min(period, length)

    hma = (
        2.0 * wma_numba(prices, half)
        - wma_numba(prices, period)
    )

    # returns
    returns = np.empty(length - 1)
    for i in range(length - 1):
        returns[i] = np.log(prices[i + 1] + 1e-9) - np.log(prices[i] + 1e-9)

    slope = slope_numba(prices, half)
    momentum = momentum_numba(returns)

    vol = np.std(returns[-half:]) + 1e-9
    vol_boost = np.tanh(vol * 80.0)

    log_prices = np.log(prices + 1e-9)
    z = (log_prices[-1] - log_prices.mean()) / (np.std(log_prices) + 1e-9)
    mr_factor = np.tanh(-0.3 * z)

    forecast = (
        hma
        + slope * (1.0 + vol_boost)
        + momentum * 0.5
        + mr_factor * vol * 0.3
    )

    return forecast


# =========================
# HMA MODELS
# =========================
def predict_hma_robust(prices, period=58):
    if len(prices) < 4:
        return None

    prices = np.asarray(prices, dtype=np.float64)

    val = hma_core(prices, period)

    if np.isnan(val) or np.isinf(val):
        return None
    return float(val)


def predict_hma_robust2(prices, period=10):
    return predict_hma_robust(prices, period)
    
MODELS = {
    "HMA": predict_hma_robust,
    "HMA2": predict_hma_robust2,
}

# =========================
# PAPER TRADER (UNCHANGED)
# =========================
# ... EVERYTHING BELOW IS 100% IDENTICAL TO YOUR ORIGINAL CODE ...
# (no logic, math, structure, or flow modified)

# [SNIPPED ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â remainder unchanged exactly as provided]



# =========================
# PAPER TRADER (DCA ENGINE)
# =========================
class PaperTrader:
    def __init__(self, balance=15000000):
        self.initial_balance = balance
        self.balance = balance

        self.positions = {e: None for e in ORDERBOOK_APIS}
        self.pnl = {e: 0.0 for e in ORDERBOOK_APIS}
        self.trade_history = deque(maxlen=10)

        # ---- risk controls ----
        self.trading_halted = False
        self.max_adds = 9
        self.global_equity_stop_pct = -0.25  # -25%

        # ---- DCA params ----
        self.entry_size = 0.00039370               # BTC
        self.add_ratio = 2.5                # 86% of total BTC
        self.adjust_step_pct = 0.18 / 100   # 0.001%
        self.take_profit_pct = 0.05 / 100   # 0.009%

    # =========================
    # FORCE CLOSE (UNIFIED)
    # =========================
    def force_close(self, ex, price, reason):
        pos = self.positions[ex]
        if pos is None:
            return

        side = pos["side"]
        size = pos["total_btc"]
        avg = pos["avg_entry"]

        pnl = (
            (price - avg) * size
            if side == "buy"
            else (avg - price) * size
        )

        self.balance += pnl
        self.pnl[ex] += pnl
        self.positions[ex] = None

        self.trade_history.append({
            "exchange": ex,
            "type": f"FORCE_EXIT_{reason}",
            "side": side.upper(),
            "price": price,
            "btc": size,
            "pnl": pnl,
            "time": datetime.now().strftime("%H:%M:%S")
        })

    # =========================
    # GLOBAL EQUITY STOP
    # =========================
    def check_global_equity_stop(self, prices):
        equity = self.balance

        for ex, pos in self.positions.items():
            if pos is None:
                continue

            price = prices.get(ex)
            if price is None:
                continue

            side = pos["side"]
            size = pos["total_btc"]
            avg = pos["avg_entry"]

            unreal = (
                (price - avg) * size
                if side == "buy"
                else (avg - price) * size
            )

            equity += unreal

        drawdown = (equity - self.initial_balance) / self.initial_balance

        if drawdown <= self.global_equity_stop_pct:
            for ex, pos in list(self.positions.items()):
                if pos is not None and ex in prices:
                    self.force_close(ex, prices[ex], "GLOBAL_EQUITY_STOP")

            self.trading_halted = True
            return True

        return False

    # =========================
    # OPEN / ADD TRADE
    # =========================
    def open_trade(self, ex, side, price):
        if self.trading_halted:
            return

        pos = self.positions[ex]

        # =========================
        # NEW ENTRY
        # =========================
        if pos is None:
            btc = self.entry_size

            self.positions[ex] = {
                "side": side,
                "avg_entry": price,
                "total_btc": btc,
                "adds": 0,
                "entries": 1,
                "tp_price": (
                    price * (1 + self.take_profit_pct)
                    if side == "buy"
                    else price * (1 - self.take_profit_pct)
                ),
                "next_add_price": (
                    price * (1 - self.adjust_step_pct)
                    if side == "buy"
                    else price * (1 + self.adjust_step_pct)
                )
            }

            self.trade_history.append({
                "exchange": ex,
                "type": "ENTRY",
                "side": side.upper(),
                "price": price,
                "btc": btc,
                "pnl": None,
                "time": datetime.now().strftime("%H:%M:%S")
            })
            return


        # =========================
        # HARD ADD LIMIT
        # =========================
        if pos["adds"] >= self.max_adds:
            self.force_close(ex, price, "MAX_ADDS")
            return

        side = pos["side"]

        should_add = (
            (side == "buy" and price <= pos["next_add_price"]) or
            (side == "sell" and price >= pos["next_add_price"])
        )

        if not should_add:
            return

           # =========================
        # SCALE-IN (BTC BASED)
        # =========================
        added_btc = pos["total_btc"] * self.add_ratio
        new_total_btc = pos["total_btc"] + added_btc

        # ---- new medium (VWAP) price ----
        new_avg = (
            pos["avg_entry"] * pos["total_btc"]
            + price * added_btc
        ) / new_total_btc

        pos["avg_entry"] = new_avg
        pos["total_btc"] = new_total_btc
        pos["adds"] += 1
        pos["entries"] += 1

        # ---- MOVE TAKE PROFIT WITH MEDIUM PRICE ----
        pos["tp_price"] = (
            new_avg * (1 + self.take_profit_pct)
            if side == "buy"
            else new_avg * (1 - self.take_profit_pct)
        )

        # ---- next add trigger ----
        pos["next_add_price"] = (
            price * (1 - self.adjust_step_pct)
            if side == "buy"
            else price * (1 + self.adjust_step_pct)
        )

        self.trade_history.append({
            "exchange": ex,
            "type": "ADD",
            "side": side.upper(),
            "price": price,
            "btc_added": added_btc,
            "total_btc": new_total_btc,
            "pnl": None,
            "time": datetime.now().strftime("%H:%M:%S")
        })

    # =========================
    # TAKE PROFIT
    # =========================
    def check_close_trade(self, ex, price):
        pos = self.positions[ex]
        if pos is None:
            return

        side = pos["side"]
        tp = pos["tp_price"]

        hit_tp = (
            price >= tp if side == "buy"
            else price <= tp
        )

        if hit_tp:
            self.force_close(ex, price, "TP")

    # =========================
    # TOTAL PNL
    # =========================
    def total_pnl(self):
        return sum(self.pnl.values())




# =========================
# SAVE & LOAD STATE
# =========================
def save_state(trader):
    state = {
        "balance": trader.balance,
        "pnl": trader.pnl,
        "trade_history": list(trader.trade_history)
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_state(trader):
    if not os.path.exists(STATE_FILE):
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    trader.balance = state.get("balance", trader.balance)
    trader.pnl = state.get("pnl", trader.pnl)
    trader.trade_history = deque(
        state.get("trade_history", []),
        maxlen=200
    )


# =========================
# FETCH MICROPRICE
# =========================
async def fetch_price(ex, url, session):
    try:
        async with session.get(url, timeout=5) as r:
            data = await r.json()

            if ex == "Coinbase":
                bid, bid_sz = map(float, data["bids"][0])
                ask, ask_sz = map(float, data["asks"][0])

            elif ex == "Kraken":
                book = list(data["result"].values())[0]
                bid, bid_sz = map(float, book["bids"][0][:2])
                ask, ask_sz = map(float, book["asks"][0][:2])

            elif ex == "Bitstamp":
                bid, bid_sz = map(float, data["bids"][0])
                ask, ask_sz = map(float, data["asks"][0])

            else:  # Bitfinex
                bid = float(data["bids"][0]["price"])
                bid_sz = abs(float(data["bids"][0]["amount"]))
                ask = float(data["asks"][0]["price"])
                ask_sz = abs(float(data["asks"][0]["amount"]))

            price = micro_price(bid, ask, bid_sz, ask_sz)
            return ex, price

    except Exception:
        return ex, None


# =========================
# GLOBAL STATE
# =========================
history = {e: deque(maxlen=60) for e in ORDERBOOK_APIS}
latest_results = {}

trader = PaperTrader()
load_state(trader)


# =========================
# TRADING LOOP
# =========================
MAX_OPEN_TRADES = 3


async def update_prices():
    global latest_results

    async with aiohttp.ClientSession() as session:
        while True:
            # -------------------------
            # FETCH ALL PRICES
            # -------------------------
            results = await asyncio.gather(*[
                fetch_price(ex, url, session)
                for ex, url in ORDERBOOK_APIS.items()
            ])

            prices = {}

            # -------------------------
            # UPDATE HISTORY & MODELS
            # -------------------------
            for ex, price in results:
                if price is None:
                    continue

                prices[ex] = price
                history[ex].append(price)

                pred_hma = (
                    MODELS["HMA"](list(history[ex]))
                    if len(history[ex]) >= 12
                    else None
                )

                pred_hma2 = (
                    MODELS["HMA2"](list(history[ex]))
                    if len(history[ex]) >= 12
                    else None
                )

                pos = trader.positions[ex]

                latest_results[ex] = {
                    "price": price,
                    "prediction_hma": pred_hma,
                    "prediction_hma2": pred_hma2,
                    "position": pos["side"].upper() if pos else "-",
                    "entries": pos["entries"] if pos else 0,
                    "adds": pos["adds"] if pos else 0,
                    "pnl": trader.pnl[ex]
                }

            # -------------------------
            # GLOBAL EQUITY STOP
            # -------------------------
            if trader.check_global_equity_stop(prices):
                save_state(trader)
                await asyncio.sleep(1)
                continue

            # -------------------------
            # TAKE PROFIT CHECK
            # -------------------------
            for ex, pos in trader.positions.items():
                if pos is not None and ex in prices:
                    trader.check_close_trade(ex, prices[ex])

            # -------------------------
            # OPEN / SCALE TRADES
            # -------------------------
            open_trades = sum(
                1 for p in trader.positions.values() if p is not None
            )

            for ex, price in prices.items():
                pos = trader.positions[ex]

                pred_hma = latest_results[ex]["prediction_hma"]
                pred_hma2 = latest_results[ex]["prediction_hma2"]

                # NEW ENTRY
                if (
                    pred_hma is not None
                    and pos is None
                    and open_trades < MAX_OPEN_TRADES
                ):
                    if pred_hma > price and price > min(pred_hma, pred_hma2):
                        trader.open_trade(ex, "buy", price)
                        open_trades += 1

                    elif pred_hma < price and price < max(pred_hma, pred_hma2):
                        trader.open_trade(ex, "sell", price)
                        open_trades += 1

                # SCALE-IN
                elif pos is not None:
                    trader.open_trade(ex, pos["side"], price)

            save_state(trader)
            await asyncio.sleep(1)


def compute_trade_totals():
    """
    Counts ENTRY + ADD only (no exits).
    Returns BTC + USD totals per exchange.
    """
    totals = {}

    for t in trader.trade_history:
        ex = t["exchange"]

        if t["type"] not in ("ENTRY", "ADD"):
            continue

        btc = (
            t.get("btc")
            or t.get("btc_added")
            or t.get("size")
            or 0.0
        )

        usd = btc * t["price"]

        if ex not in totals:
            totals[ex] = {
                "btc_total": 0.0,
                "usd_total": 0.0,
                "entries": 0,
                "adds": 0
            }

        totals[ex]["btc_total"] += btc
        totals[ex]["usd_total"] += usd

        if t["type"] == "ENTRY":
            totals[ex]["entries"] += 1
        else:
            totals[ex]["adds"] += 1

    return totals

logging.getLogger("uvicorn").setLevel(logging.CRITICAL)

app = FastAPI(title="BTC Live Microprice API")


@app.get("/live")
async def live_data():
    return JSONResponse({
        "timestamp": datetime.now().isoformat(),
        "balance": trader.balance,
        "total_pnl": trader.total_pnl(),
        "totals": compute_trade_totals(),
        "exchanges": latest_results,
        "last_trades": list(trader.trade_history)[-20:]
    })



async def main():
    asyncio.create_task(update_prices())

    public_url = ngrok.connect(8000, "http")
    print(f"ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€¦Ã‚Â¡ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Public URL: {public_url}")

    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        log_level="critical"
    )

    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
