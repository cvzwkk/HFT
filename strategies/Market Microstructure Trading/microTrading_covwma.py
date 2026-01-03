import json
import asyncio
import websockets
import numpy as np
from collections import deque
from blessed import Terminal

class HFTLogic:
    def __init__(self, length=66):
        self.length = length
        self.prices = deque(maxlen=length)
        self.sigma_buffer = deque(maxlen=length)
        self.covs_buffer = deque(maxlen=length)
        self.sum_covs = 0.0
        self.sum_sigma = 0.0

    def compute(self, last_price, bid_p, bid_q, ask_p, ask_q):
        # Microprice calculation
        microprice = (bid_p * ask_q + ask_p * bid_q) / (bid_q + ask_q)
        self.prices.append(last_price)
        
        if len(self.prices) < self.length:
            return None, microprice

        current_sigma = np.std(self.prices)
        current_covs = last_price * current_sigma
        
        if len(self.sigma_buffer) == self.length:
            self.sum_sigma -= self.sigma_buffer[0]
            self.sum_covs -= self.covs_buffer[0]
            
        self.sigma_buffer.append(current_sigma)
        self.covs_buffer.append(current_covs)
        self.sum_sigma += current_sigma
        self.sum_covs += current_covs
        
        covwma = self.sum_covs / self.sum_sigma if self.sum_sigma != 0 else last_price
        return covwma, microprice

class BitfinexClient:
    def __init__(self, symbol="tBTCUSD"):
        self.symbol = symbol
        self.term = Terminal()
        self.logic = HFTLogic(length=66)
        
        self.last_price = 0.0
        self.bid_p, self.bid_q = 0.0, 0.0
        self.ask_p, self.ask_q = 0.0, 0.0
        
        self.balance = 10000.0
        self.position = 0.0
        self.entry_price = 0.0
        self.wins = 0
        self.total_closed_trades = 0

    async def run(self):
        url = "wss://api-pub.bitfinex.com/ws/2"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": self.symbol, "prec": "P0"}))
            await ws.send(json.dumps({"event": "subscribe", "channel": "trades", "symbol": self.symbol}))

            with self.term.fullscreen(), self.term.hidden_cursor():
                while True:
                    data = json.loads(await ws.recv())
                    if isinstance(data, list):
                        msg = data[1]
                        if msg == "te": self.last_price = data[2][3]
                        elif isinstance(msg, list) and len(msg) == 3:
                            price, count, amount = msg
                            if amount > 0: self.bid_p, self.bid_q = price, amount
                            else: self.ask_p, self.ask_q = price, abs(amount)
                        
                        try:
                            self.execute_and_render()
                        except Exception:
                            continue # Prevent TUI errors from killing the connection

    def execute_and_render(self):
        if self.last_price == 0 or self.bid_q == 0: return
        
        covwma, micro = self.logic.compute(self.last_price, self.bid_p, self.bid_q, self.ask_p, self.ask_q)

        signal = "Warming Up..."
        if covwma is not None:
            # INVERTED LOGIC (98% Loss protection)
            if (micro < self.last_price < covwma) and self.position == 0:
                self.position = self.balance / self.last_price
                self.entry_price = self.last_price
                self.balance = 0
                signal = "INV BUY"
            elif (micro > self.last_price > covwma) and self.position > 0:
                trade_pnl = (self.last_price - self.entry_price) * self.position
                if trade_pnl > 0: self.wins += 1
                self.total_closed_trades += 1
                self.balance = self.position * self.last_price
                self.position = 0
                signal = "INV SELL"
            else:
                signal = "WAITING"

        # TUI Calculations
        val = self.balance + (self.position * self.last_price)
        winrate = (self.wins / self.total_closed_trades * 100) if self.total_closed_trades > 0 else 0
        cov_str = f"{covwma:.2f}" if covwma is not None else "Calculating..."
        
        # Header
        with self.term.location(0, 0):
            print(self.term.black_on_magenta(f" INVERTED HFT BOT | WR: {winrate:.1f}% | TRADES: {self.total_closed_trades} ".center(self.term.width)))
        
        # Account Stats
        with self.term.location(0, 3):
            print(f" Portfolio: {self.term.bold(f'${val:,.2f}')}")
            print(f" Win Rate:  {self.term.green if winrate > 50 else self.term.red}{winrate:.2f}%")
            print(f" Price:     {self.last_price:.2f}")
            print(f" COVWMA:    {self.term.yellow(cov_str)}") 
            print(f" Micro:     {self.term.cyan(f'{micro:.2f}')}")
            
        # Warmup / Clean up line
        with self.term.location(0, 9):
            if covwma is None:
                progress = len(self.logic.prices)
                bar = "█" * progress + "-" * (66 - progress)
                print(f" Warming Data: [{bar}] {progress}/66")
            else:
                # Corrected from 'term' to 'self.term'
                print(self.term.clear_eol + " System Ready - Trading Inverted Logic")

        # Signal Status
        with self.term.location(0, 11):
            signal_col = self.term.bold_green if "BUY" in signal else self.term.bold_red if "SELL" in signal else self.term.white
            print(f" LAST SIGNAL: {signal_col(signal.ljust(20))}")

if __name__ == "__main__":
    client = BitfinexClient()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
