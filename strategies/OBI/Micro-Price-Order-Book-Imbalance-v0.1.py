# !pip install blessed websockets
import asyncio
import json
import time
import collections
from blessed import Terminal
from websockets import connect

# --- Configuration ---
SYMBOL = "tBTCUSD"
INITIAL_BALANCE = 10000.0
ORDER_SIZE = 0.05  # Adjusted for visible $1.00 PnL swings
RISK_THRESHOLD = 0.01
TERM = Terminal()

class HFTPaperTrader:
    def __init__(self):
        self.balance = INITIAL_BALANCE
        self.inventory = 0.0 
        self.entry_price = 0.0
        self.trades = collections.deque(maxlen=10)
        self.book = {'bids': {}, 'asks': {}}
        self.status_msg = "SCANNING MARKET"

    def get_metrics(self):
        if not self.book['bids'] or not self.book['asks'] or len(self.book['bids']) == 0:
            return 0.0, 0.0
        best_bid = max(self.book['bids'].keys())
        best_ask = min(self.book['asks'].keys())
        mid = (best_bid + best_ask) / 2
        
        v_bid = abs(self.book['bids'][best_bid])
        v_ask = abs(self.book['asks'][best_ask])
        imbalance = (v_bid - v_ask) / (v_bid + v_ask)
        return mid, imbalance

    async def risk_controller(self):
        """Monitors PnL and force closes at +/- $1.00"""
        while True:
            mid, _ = self.get_metrics()
            if self.inventory != 0 and mid > 0:
                # Calculate PnL: (Current - Entry) * Inventory
                pnl = (mid - self.entry_price) * self.inventory
                
                if pnl >= RISK_THRESHOLD:
                    self.status_msg = f"TARGET HIT: +${pnl:.2f}"
                    self.close_position(mid, pnl)
                elif pnl <= -RISK_THRESHOLD:
                    self.status_msg = f"STOP HIT: -${abs(pnl):.2f}"
                    self.close_position(mid, pnl)
            
            await asyncio.sleep(0.01)

    def close_position(self, price, pnl):
        timestamp = time.strftime("%H:%M:%S")
        side = "EXIT LONG" if self.inventory > 0 else "EXIT SHORT"
        
        # Realize the balance
        self.balance += (self.inventory * price)
        self.trades.appendleft({
            "time": timestamp, 
            "side": side, 
            "price": price, 
            "pnl": pnl
        })
        self.inventory = 0.0
        self.entry_price = 0.0

    async def logic_loop(self):
        while True:
            mid, imb = self.get_metrics()
            if mid == 0 or self.inventory != 0: 
                await asyncio.sleep(0.01)
                continue

            # Aggressive Imbalance Entry
            if imb > 0.85: 
                self.entry_price = mid
                self.inventory = ORDER_SIZE
                self.balance -= (mid * ORDER_SIZE)
                self.status_msg = "OPENING LONG"
                self.trades.appendleft({"time": time.strftime("%H:%M:%S"), "side": "BUY", "price": mid, "pnl": 0.0})
            
            elif imb < -0.85:
                self.entry_price = mid
                self.inventory = -ORDER_SIZE
                self.balance += (mid * abs(ORDER_SIZE))
                self.status_msg = "OPENING SHORT"
                self.trades.appendleft({"time": time.strftime("%H:%M:%S"), "side": "SELL", "price": mid, "pnl": 0.0})

            await asyncio.sleep(0.01)

    def draw_ui(self):
        mid, imb = self.get_metrics()
        current_pnl = (mid - self.entry_price) * self.inventory if self.inventory != 0 else 0.0
        total_val = self.balance + (self.inventory * mid)
        
        print(TERM.home + TERM.clear)
        print(TERM.bold_white_on_blue(f" HFT TRADER | FIX: $1.00 RISK | {SYMBOL} ".center(TERM.width)))
        
        # Stats
        p_color = TERM.green if current_pnl >= 0 else TERM.red
        print(f"\n {TERM.bold('ACCOUNT:')}  ${total_val:,.2f}")
        print(f" {TERM.bold('POSITION:')} {self.inventory:.4f} BTC @ ${self.entry_price:,.2f}")
        print(f" {TERM.bold('OPEN PnL:')} {p_color(f'${current_pnl:+.4f}')}")
        print(f" {TERM.bold('STATUS:')}   {TERM.cyan(self.status_msg)}")

        print(f"\n {TERM.bold('MARKET:')}   Price: ${mid:,.2f} | Imbalance: {imb:+.2f}")
        
        # Trade History - FIXED LINE BELOW
        print("\n" + TERM.black_on_white(" TIME     | ACTION     | PRICE      | REALIZED PnL ".center(TERM.width)))
        for t in list(self.trades):
            c = TERM.green if t['pnl'] > 0 else (TERM.red if t['pnl'] < 0 else TERM.white)
            # FIXED: Used 'pnl' as string key
            print(f" {t['time']} | {t['side']:<10} | {t['price']:<10,.2f} | {c(f'${t['pnl']:+.4f}')}")

    async def socket_handler(self):
        uri = "wss://api-pub.bitfinex.com/ws/2"
        async with connect(uri) as ws:
            await ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": SYMBOL, "prec": "P0"}))
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    update = data[1]
                    if isinstance(update[0], list):
                        for e in update: self.update_book(e)
                    else:
                        self.update_book(update)
                self.draw_ui()

    def update_book(self, e):
        if len(e) < 3: return
        price, count, amt = e
        side = 'bids' if amt > 0 else 'asks'
        if count > 0: self.book[side][price] = amt
        else: self.book[side].pop(price, None)

async def main():
    bot = HFTPaperTrader()
    await asyncio.gather(
        bot.socket_handler(), 
        bot.logic_loop(), 
        bot.risk_controller()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping Bot...")
