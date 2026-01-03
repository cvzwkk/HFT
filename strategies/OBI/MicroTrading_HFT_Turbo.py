import asyncio, json, time, sys
from blessed import Terminal
from websockets import connect

# --- Configuração ---
SYMBOL = "tBTCUSD"
USD_BALANCE = 10000.0
BTC_BALANCE = 0.5   # Saldo inicial de BTC
ORDER_SIZE = 0.01   # Tamanho fixo da ordem
STOP_LOSS = 0.60   
TAKE_PROFIT = 0.90 
MAX_CONCURRENT_TRADES = 100 
TERM = Terminal()

class UltraTurboTrader:
    def __init__(self):
        self.usd = USD_BALANCE
        self.btc = BTC_BALANCE
        self.total_trades = 0
        self.gains = 0
        self.losses = 0
        
        self.active_trades = []
        self.book = {'bids': {}, 'asks': {}}
        self.status_msg = "SISTEMA INICIADO"
        self.start_time = time.time()
        self.last_full_clear = time.time()

    def get_market_state(self):
        if not self.book['bids'] or not self.book['asks']: return None
        bids = sorted(self.book['bids'].items(), reverse=True)
        asks = sorted(self.book['asks'].items())
        
        best_bid, v_bid = bids[0]
        best_ask, v_ask = asks[0]
        v_bid_abs, v_ask_abs = abs(v_bid), abs(v_ask)
        
        microprice = (best_bid * v_ask_abs + best_ask * v_bid_abs) / (v_bid_abs + v_ask_abs)
        imb = (v_bid_abs - v_ask_abs) / (v_bid_abs + v_ask_abs)
        
        return {
            'bid': best_bid, 'ask': best_ask, 
            'micro': microprice, 'imb': imb
        }

    async def logic_loop(self):
        while True:
            m = self.get_market_state()
            if m and len(self.active_trades) < MAX_CONCURRENT_TRADES:
                # Gatilho de COMPRA
                if m['imb'] > 0.75:
                    cost = ORDER_SIZE * m['bid']
                    # Verificação Estrita de Saldo USD
                    if self.usd >= cost and self.usd > 0:
                        self.execute_trade('BUY', m['bid'])
                    else:
                        self.status_msg = "ERRO: SALDO USD INSUFICIENTE"
                
                # Gatilho de VENDA
                elif m['imb'] < -0.75:
                    # Verificação Estrita de Saldo BTC
                    if self.btc >= ORDER_SIZE:
                        self.execute_trade('SELL', m['ask'])
                    else:
                        self.status_msg = "ERRO: SALDO BTC INSUFICIENTE"
            await asyncio.sleep(0) 

    def execute_trade(self, side, price):
        if side == 'BUY':
            self.usd -= ORDER_SIZE * price
            self.btc += ORDER_SIZE
            self.active_trades.append({'entry': price, 'inv': ORDER_SIZE, 'side': 'BUY'})
        else:
            self.usd += ORDER_SIZE * price
            self.btc -= ORDER_SIZE
            self.active_trades.append({'entry': price, 'inv': -ORDER_SIZE, 'side': 'SELL'})
        self.status_msg = f"EXECUTADO: {side} @ {price:.2f}"

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
            # Limpeza do output a cada 500ms
            if now - self.last_full_clear > 0.5:
                sys.stdout.write(TERM.clear)
                self.last_full_clear = now

            m = self.get_market_state()
            if m:
                uptime = time.strftime("%H:%M:%S", time.gmtime(now - self.start_time))
                wr = (self.gains / self.total_trades * 100) if self.total_trades > 0 else 0
                
                out = TERM.home
                out += TERM.bold_black_on_white(f" HFT TURBO | UP: {uptime} ".center(TERM.width)) + "\n"
                out += f" PREÇO: {m['bid']:.2f} | {TERM.bold_yellow(f'MICRO: {m['micro']:.4f}')}\n"
                out += f" SALDO USD: ${self.usd:,.2f} | SALDO BTC: {self.btc:.6f}\n"
                out += f"{TERM.white('─' * TERM.width)}\n"
                out += f" TRADES: {self.total_trades} | {TERM.green(f'G: {self.gains}')} | {TERM.red(f'L: {self.losses}')}\n"
                out += f" WIN RATE: {wr:.1f}% | ATIVAS: {len(self.active_trades)}\n"
                out += f" MSG: {self.status_msg[:50]}\n"
                
                sys.stdout.write(out)
                sys.stdout.flush()
            # Update visual a cada 50ms
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
    bot = UltraTurboTrader()
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
