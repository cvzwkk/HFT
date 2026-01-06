import asyncio, json, time, sys
import numpy as np
from collections import deque
from blessed import Terminal
from websockets import connect
from statsmodels.tsa.arima.model import ARIMA

# =========================
# CONFIG
# =========================
SYMBOL = "tBTCUSD"

USD_BALANCE = 10000.0
BTC_BALANCE = 0.5
ORDER_SIZE = 0.01

TAKE_PROFIT = 0.8
STOP_LOSS = 0.5
MAX_CONCURRENT_TRADES = 80

# --- ARIMA ---
ARIMA_WINDOW = 120
ARIMA_ORDER = (1, 0, 1)
ARIMA_REFIT_SEC = 0.3
ARIMA_THRESHOLD = 0.15

# --- KALMAN ---
KALMAN_Q = 1e-5
KALMAN_R = 1e-3

# --- IMBALANCE ---
IMB_THRESHOLD = 0.60

TERM = Terminal()

# =========================
# BOT
# =========================
class EnsembleMicroTrader:
    def __init__(self):
        self.usd = USD_BALANCE
        self.btc = BTC_BALANCE

        self.book = {'bids': {}, 'asks': {}}

        # --- Price buffers ---
        self.microprices = deque(maxlen=ARIMA_WINDOW)
        self.returns = deque(maxlen=ARIMA_WINDOW)

        # --- ARIMA ---
        self.arima = None
        self.last_arima_refit = 0

        # --- Kalman ---
        self.kalman_x = None
        self.kalman_p = 1.0

        # --- Trades ---
        self.active_trades = []
        self.total = 0
        self.wins = 0
        self.losses = 0

        self.status = "ENSEMBLE READY"
        self.start = time.time()
        self.last_clear = time.time()

    # =========================
    # MARKET
    # =========================
    def market(self):
        if not self.book['bids'] or not self.book['asks']:
            return None

        bid, vb = max(self.book['bids'].items())
        ask, va = min(self.book['asks'].items())

        vb, va = abs(vb), abs(va)
        micro = (bid * va + ask * vb) / (vb + va)
        imb = (vb - va) / (vb + va)

        return bid, ask, micro, imb

    # =========================
    # KALMAN FILTER
    # =========================
    def kalman_update(self, z):
        if self.kalman_x is None:
            self.kalman_x = z
            return 0.0

        # Prediction
        self.kalman_p += KALMAN_Q

        # Update
        k = self.kalman_p / (self.kalman_p + KALMAN_R)
        prev_x = self.kalman_x
        self.kalman_x += k * (z - self.kalman_x)
        self.kalman_p *= (1 - k)

        return self.kalman_x - prev_x

    # =========================
    # ARIMA
    # =========================
    def arima_update(self, micro):
        if self.microprices:
            self.returns.append(micro - self.microprices[-1])

        self.microprices.append(micro)

        now = time.time()
        if (
            len(self.returns) >= ARIMA_WINDOW
            and now - self.last_arima_refit > ARIMA_REFIT_SEC
        ):
            try:
                self.arima = ARIMA(
                    np.array(self.returns),
                    order=ARIMA_ORDER
                ).fit(method_kwargs={"maxiter": 20})
                self.last_arima_refit = now
            except:
                self.arima = None

    def arima_forecast(self):
        if not self.arima:
            return 0.0
        try:
            return float(self.arima.forecast(1)[0])
        except:
            return 0.0

    # =========================
    # LOGIC
    # =========================
    async def logic(self):
        while True:
            m = self.market()
            if not m:
                await asyncio.sleep(0)
                continue

            bid, ask, micro, imb = m

            # Update models
            kalman_delta = self.kalman_update(micro)
            self.arima_update(micro)
            arima_pred = self.arima_forecast()

            if len(self.active_trades) < MAX_CONCURRENT_TRADES:

                # BUY
                if (
                    kalman_delta > 0
                    and arima_pred > ARIMA_THRESHOLD
                    and imb > IMB_THRESHOLD
                    and self.usd >= bid * ORDER_SIZE
                ):
                    self.open("BUY", bid)

                # SELL
                elif (
                    kalman_delta < 0
                    and arima_pred < -ARIMA_THRESHOLD
                    and imb < -IMB_THRESHOLD
                    and self.btc >= ORDER_SIZE
                ):
                    self.open("SELL", ask)

            await asyncio.sleep(0)

    def open(self, side, price):
        if side == "BUY":
            self.usd -= price * ORDER_SIZE
            self.btc += ORDER_SIZE
        else:
            self.usd += price * ORDER_SIZE
            self.btc -= ORDER_SIZE

        self.active_trades.append({
            "side": side,
            "entry": price
        })

    # =========================
    # RISK
    # =========================
    async def risk(self):
        while True:
            m = self.market()
            if m:
                bid, ask, _, _ = m
                for t in list(self.active_trades):
                    px = ask if t["side"] == "BUY" else bid
                    pnl = (px - t["entry"]) * (1 if t["side"] == "BUY" else -1)

                    if pnl >= TAKE_PROFIT or pnl <= -STOP_LOSS:
                        self.close(t, px, pnl)
            await asyncio.sleep(0)

    def close(self, t, price, pnl):
        if t["side"] == "BUY":
            self.usd += price * ORDER_SIZE
            self.btc -= ORDER_SIZE
        else:
            self.usd -= price * ORDER_SIZE
            self.btc += ORDER_SIZE

        self.total += 1
        self.wins += pnl > 0
        self.losses += pnl <= 0
        self.active_trades.remove(t)

    # =========================
    # UI
    # =========================
    async def ui(self):
        while True:
            now = time.time()
            if now - self.last_clear > 0.4:
                sys.stdout.write(TERM.clear)
                self.last_clear = now

            m = self.market()
            if m:
                bid, _, micro, _ = m
                up = time.strftime("%H:%M:%S", time.gmtime(now - self.start))
                wr = (self.wins / self.total * 100) if self.total else 0

                out = TERM.home
                out += TERM.bold_black_on_white(
                    f" ENSEMBLE ARIMA + KALMAN | UP {up} ".center(TERM.width)
                ) + "\n"
                out += f" BID {bid:.2f} | MICRO {micro:.4f}\n"
                out += f" USD ${self.usd:,.2f} | BTC {self.btc:.6f}\n"
                out += f" TRADES {self.total} | W {self.wins} | L {self.losses}\n"
                out += f" WIN {wr:.1f}% | ACTIVE {len(self.active_trades)}\n"
                out += f" {self.status}\n"

                sys.stdout.write(out)
                sys.stdout.flush()

            await asyncio.sleep(0.05)

    # =========================
    # WS
    # =========================
    async def ws(self):
        uri = "wss://api-pub.bitfinex.com/ws/2"
        async with connect(uri) as ws:
            await ws.send(json.dumps({
                "event": "subscribe",
                "channel": "book",
                "symbol": SYMBOL,
                "prec": "P0"
            }))

            while True:
                msg = json.loads(await ws.recv())
                if isinstance(msg, list) and len(msg) > 1:
                    d = msg[1]
                    if isinstance(d[0], list):
                        for e in d:
                            self.book_update(e)
                    else:
                        self.book_update(d)

    def book_update(self, e):
        if len(e) < 3:
            return
        p, c, a = e
        side = 'bids' if a > 0 else 'asks'
        if c > 0:
            self.book[side][p] = a
        else:
            self.book[side].pop(p, None)

# =========================
# MAIN
# =========================
async def main():
    bot = EnsembleMicroTrader()
    with TERM.fullscreen(), TERM.hidden_cursor():
        await asyncio.gather(
            bot.ws(),
            bot.logic(),
            bot.risk(),
            bot.ui()
        )

if __name__ == "__main__":
    asyncio.run(main())
