import asyncio, json, time, sys
from blessed import Terminal
from websockets import connect

# --- Configuração ---
SYMBOL = "tBTCUSD"
USD_BALANCE = 10000.0
BTC_BALANCE = 0.5
ORDER_SIZE = 0.01
STOP_LOSS = 0.50
TAKE_PROFIT = 0.80
MAX_CONCURRENT_TRADES = 100
DEPTH_LEVELS = 10  # Number of order book levels to analyze
TERM = Terminal()

class DepthPressureTrader:
    def __init__(self):
        self.usd = USD_BALANCE
        self.btc = BTC_BALANCE
        self.total_trades = 0
        self.gains = 0
        self.losses = 0
        self.active_trades = []
        self.book = {'bids': {}, 'asks': {}}
        self.status_msg = "DEPTH ENGINE ACTIVE"
        self.start_time = time.time()
        self.last_full_clear = time.time()

    def get_market_state(self):
        if not self.book['bids'] or not self.book['asks']: return None
        
        # Sort full book
        bids = sorted(self.book['bids'].items(), reverse=True)
        asks = sorted(self.book['asks'].items())
        
        # Safety check for depth
        if len(bids) < DEPTH_LEVELS or len(asks) < DEPTH_LEVELS: return None
        
        # --- STRATEGY CORE: Wall Calculation ---
        # Sum volume of the top N levels (Aggregate Liquidity)
        wall_buy_vol = sum(abs(vol) for price, vol in bids[:DEPTH_LEVELS])
        wall_sell_vol = sum(abs(vol) for price, vol in asks[:DEPTH_LEVELS])
        
        # Pressure Ratio: > 1.0 means Buy Wall is bigger
        # Example: 3.0 means Buyers have 3x more volume shown than Sellers
        pressure_ratio = wall_buy_vol / wall_sell_vol if wall_sell_vol > 0 else 1.0
        
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        
        return {
            'bid': best_bid, 'ask': best_ask, 
            'wall_b': wall_buy_vol, 'wall_a': wall_sell_vol,
            'ratio': pressure_ratio
        }

    async def logic_loop(self):
        while True:
            m = self.get_market_state()
            if m and len(self.active_trades) < MAX_CONCURRENT_TRADES:
                # --- TRIGGERS ---
                
                # BUY SIGNAL: The "Bulldozer"
                # If Buy Wall is > 2.5x larger than Sell Wall, buyers are "pushing" price up.
                if m['ratio'] > 2.5: 
                    cost = ORDER_SIZE * m['bid']
                    if self.usd >= cost:
                        self.execute_trade('BUY', m['bid'])
                
                # SELL SIGNAL: The "Avalanche"
                # If Sell Wall is > 2.5x larger than Buy Wall (Ratio < 0.4), sellers are crushing price.
                elif m['ratio'] < 0.4:
                    if self.btc >= ORDER_SIZE:
                        self.execute_trade('SELL', m['ask'])
                        
            await asyncio.sleep(0) 

    def execute_trade(self, side, price):
        if side == 'BUY':
            self.usd -= ORDER_SIZE * price
            self.btc += ORDER_SIZE
            self.active_trades.append({'entry': price, 'side': 'BUY'})
        else:
            self.usd += ORDER_SIZE * price
            self.btc -= ORDER_SIZE
            self.active_trades.append({'entry': price, 'side': 'SELL'})
        self.status_msg = f"WALL HIT: {side} @ {price:.2f}"

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
                
                # Colorize Ratio
                r_color = TERM.green if m['ratio'] > 1.5 else (TERM.red if m['ratio'] < 0.6 else TERM.yellow)
                
                out = TERM.home
                out += TERM.bold_black_on_magenta(f" HFT DEPTH READER | UP: {uptime} ".center(TERM.width)) + "\n"
                out += f" BID: {m['bid']:.2f} | ASK: {m['ask']:.2f}\n"
                out += f" WALL BUY: {m['wall_b']:.2f} | WALL SELL: {m['wall_a']:.2f} | {r_color(f'RATIO: {m['ratio']:.2f}x')}\n"
                out += f" SALDO USD: ${self.usd:,.2f} | SALDO BTC: {self.btc:.6f}\n"
                out += f"{TERM.magenta('─' * TERM.width)}\n"
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
    bot = DepthPressureTrader()
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
