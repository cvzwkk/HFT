import asyncio, json, time, sys
from blessed import Terminal
from websockets import connect
from collections import deque

# --- Configuração ---
SYMBOL = "tBTCUSD"
USD_BALANCE = 10000.0
BTC_BALANCE = 0.5
ORDER_SIZE = 0.01
STOP_LOSS = 0.50
TAKE_PROFIT = 1.00  # Momentum trades often run further
MAX_CONCURRENT_TRADES = 100
VELOCITY_THRESHOLD = 5.0  # Trigger if price moves > $5.00 per second
SCAN_WINDOW = 1.0  # Look back 1 second
TERM = Terminal()

class VelocityTrader:
    def __init__(self):
        self.usd = USD_BALANCE
        self.btc = BTC_BALANCE
        self.total_trades = 0
        self.gains = 0
        self.losses = 0
        self.active_trades = []
        self.book = {'bids': {}, 'asks': {}}
        self.status_msg = "VELOCITY ENGINE ACTIVE"
        self.start_time = time.time()
        self.last_full_clear = time.time()
        
        # History for velocity calculation: Stores tuples (time, midprice)
        self.price_history = deque(maxlen=100) 

    def get_market_state(self):
        if not self.book['bids'] or not self.book['asks']: return None
        
        bids = sorted(self.book['bids'].items(), reverse=True)
        asks = sorted(self.book['asks'].items())
        
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        midprice = (best_bid + best_ask) / 2
        now = time.time()
        
        # Update History
        self.price_history.append((now, midprice))
        
        # Prune old data (keep only data inside SCAN_WINDOW)
        while self.price_history and (now - self.price_history[0][0] > SCAN_WINDOW):
            self.price_history.popleft()
            
        # Calculate Velocity
        velocity = 0.0
        if len(self.price_history) > 1:
            start_t, start_p = self.price_history[0]
            # Delta Price / Delta Time
            time_delta = now - start_t
            if time_delta > 0:
                velocity = (midprice - start_p) / time_delta
        
        return {
            'bid': best_bid, 'ask': best_ask, 
            'mid': midprice, 'vel': velocity
        }

    async def logic_loop(self):
        while True:
            m = self.get_market_state()
            if m and len(self.active_trades) < MAX_CONCURRENT_TRADES:
                
                # --- STRATEGY: MOMENTUM IGNITION ---
                
                # Positive Velocity (Price is exploding UP)
                if m['vel'] > VELOCITY_THRESHOLD:
                    cost = ORDER_SIZE * m['bid']
                    # Don't buy if we just bought (simple cooldown could be added here)
                    if self.usd >= cost:
                        self.execute_trade('BUY', m['bid'])
                
                # Negative Velocity (Price is crashing DOWN)
                elif m['vel'] < -VELOCITY_THRESHOLD:
                    if self.btc >= ORDER_SIZE:
                        self.execute_trade('SELL', m['ask'])
                        
            await asyncio.sleep(0) # Zero latency loop

    def execute_trade(self, side, price):
        if side == 'BUY':
            self.usd -= ORDER_SIZE * price
            self.btc += ORDER_SIZE
            self.active_trades.append({'entry': price, 'side': 'BUY'})
        else:
            self.usd += ORDER_SIZE * price
            self.btc -= ORDER_SIZE
            self.active_trades.append({'entry': price, 'side': 'SELL'})
        self.status_msg = f"MOMENTUM: {side} @ {price:.2f}"

    async def risk_controller(self):
        while True:
            m = self.get_market_state()
            if m and self.active_trades:
                for trade in list(self.active_trades):
                    current_p = m['ask'] if trade['side'] == 'BUY' else m['bid']
                    pnl = (current_p - trade['entry']) * (1 if trade['side'] == 'BUY' else -1)
                    
                    if pnl >= TAKE_PROFIT or pnl <= -STOP_LOSS:
                        self.close_trade(trade, current_p, pnl)
            await asyncio.sleep(0)

    def close_trade(self, trade, price, pnl):
        if trade['side'] == 'BUY':
            self.usd += price * ORDER_SIZE
            self.btc -= ORDER_SIZE
        else:
            self.usd -= price * ORDER_SIZE
            self.btc += ORDER_SIZE
            
        self.total_trades += 1
        if pnl > 0: self.gains += 1
        else: self.losses += 1
        self.active_trades.remove(trade)

    async def ui_loop(self):
        while True:
            now = time.time()
            if now - self.last_full_clear > 0.5:
                sys.stdout.write(TERM.clear)
                self.last_full_clear = now

            m = self.get_market_state()
            if m:
                uptime = time.strftime("%H:%M:%S", time.gmtime(now - self.start_time))
                wr = (self.gains / self.total_trades * 100) if self.total_trades > 0 else 0
                
                # Visual Speedometer
                v_val = m['vel']
                v_str = f"{v_val:+.2f} $/s"
                if v_val > VELOCITY_THRESHOLD: v_color = TERM.bold_green_on_black
                elif v_val < -VELOCITY_THRESHOLD: v_color = TERM.bold_red_on_black
                else: v_color = TERM.white
                
                out = TERM.home
                out += TERM.bold_black_on_yellow(f" HFT VELOCITY | UP: {uptime} ".center(TERM.width)) + "\n"
                out += f" BID: {m['bid']:.2f} | ASK: {m['ask']:.2f}\n"
                out += f" SPEED: {v_color(v_str.center(20))}\n" 
                out += f" SALDO USD: ${self.usd:,.2f} | SALDO BTC: {self.btc:.6f}\n"
                out += f"{TERM.yellow('─' * TERM.width)}\n"
                out += f" TRADES: {self.total_trades} | {TERM.green(f'G: {self.gains}')} | {TERM.red(f'L: {self.losses}')}\n"
                out += f" WIN RATE: {wr:.1f}% | ATIVAS: {len(self.active_trades)}\n"
                out += f" MSG: {self.status_msg[:50]}\n"
                
                sys.stdout.write(out)
                sys.stdout.flush()
            await asyncio.sleep(0.05)

    async def socket_handler(self):
        uri = "wss://api-pub.bitfinex.com/ws/2"
        async with connect(uri) as ws:
            await ws.send(json.dumps({"event":"subscribe","channel":"book","symbol":SYMBOL,"prec":"P0"}))
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    update = data[1]
                    if isinstance(update[0], list):
                        for e in update: self.update_book(e)
                    else: self.update_book(update)

    def update_book(self, e):
        if len(e) < 3: return
        p, c, a = e
        s = 'bids' if a > 0 else 'asks'
        if c > 0: self.book[s][p] = a
        else: self.book[s].pop(p, None)

async def main():
    bot = VelocityTrader()
    with TERM.fullscreen(), TERM.hidden_cursor():
        await asyncio.gather(
            bot.socket_handler(),
            bot.logic_loop(),
            bot.risk_controller(),
            bot.ui_loop()
        )

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
