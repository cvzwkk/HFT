
import asyncio
import json
import time
import websockets
import nest_asyncio
import threading
import sys
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyngrok import ngrok

# --- CONFIGURATION ---
NGROK_TOKEN = "37fJKKexs66q3bWBAAelBYiU2Yp_7Fq6yLN25TUj43fiBHfEN"
SYMBOL = "BTC/USD"
INITIAL_BALANCE = 100000.0
TRADE_AMOUNT_USD = 50.0
MIN_LIQUIDITY_QTY = 0.01  # Minimum BTC required at top of book to enter
STATE_FILE = "bot_state_kraken.json"

bot = None

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        now = time.time()
        sorted_asks = sorted(bot.order_book['asks'].items())[:10][::-1]
        sorted_bids = sorted(bot.order_book['bids'].items(), reverse=True)[:10]

        # Active Trades Rows
        trades_rows = "".join([
            f"<tr><td>{t['side']}</td><td>{t['entry']:.2f}</td><td>{t['qty']:.5f}</td><td>${t['sl']:.2f}</td><td>{t['tp']:.2f}</td><td>{now - t['open_time']:.1f}s</td></tr>"
            for t in bot.open_trades
        ])

        # Trade History Rows (Shows Entry, Exit, Size, and PnL)
        history_rows = "".join([
            f"<tr><td>{h['side']}</td><td>{h['entry']:.2f}</td><td>{h['qty']:.5f}</td><td>{h['exit']:.2f}</td><td style='color:{'#3fb950' if h['pnl'] >= 0 else '#f85149'}'>${h['pnl']:.4f}</td><td>{h['time']}</td></tr>"
            for h in reversed(list(bot.history))
        ])

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Kraken L2 Scalper</title>
            <meta http-equiv="refresh" content="1">
            <style>
                body {{ font-family: 'Consolas', monospace; background: #0b0e11; color: #c9d1d9; padding: 20px; }}
                .container {{ max-width: 900px; margin: auto; }}
                .stat-box {{ background: #161b22; padding: 15px; border-radius: 6px; border: 1px solid #30363d; margin-bottom: 15px; display: flex; justify-content: space-around; }}
                table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #161b22; }}
                th, td {{ padding: 8px; text-align: left; border: 1px solid #30363d; font-size: 12px; }}
                .ask {{ color: #f85149; }} .bid {{ color: #3fb950; }}
                .mid {{ background: #21262d; text-align: center; font-weight: bold; color: #8b949e; }}
                .filter-tag {{ font-size: 10px; padding: 2px 5px; border-radius: 4px; background: #388bfd; color: white; }}
                h3 {{ border-left: 4px solid #388bfd; padding-left: 10px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>⚡ Kraken L2 HFT Bot <span class="filter-tag">Volume Filter Active</span></h2>
                <div class="stat-box">
                    <span>Balance: <b>${bot.balance:,.2f}</b></span>
                    <span>PnL: <b style="color:{'#3fb950' if bot.pnl >= 0 else '#f85149'}">${bot.pnl:,.2f}</b></span>
                    <span>Spread: <b>${bot.spread:.2f}</b></span>
                </div>

                <h3>Active Grid Trades</h3>
                <table>
                    <thead><tr><th>Side</th><th>Entry</th><th>Qty</th><th>SL</th><th>TP</th><th>Age</th></tr></thead>
                    <tbody>{trades_rows if trades_rows else "<tr><td colspan='6' style='text-align:center'>No active trades</td></tr>"}</tbody>
                </table>

                <h3>Completed Trade History</h3>
                <table>
                    <thead><tr><th>Side</th><th>Entry</th><th>Size</th><th>Exit</th><th>PnL</th><th>Time</th></tr></thead>
                    <tbody>{history_rows if history_rows else "<tr><td colspan='6' style='text-align:center'>Waiting for first exit...</td></tr>"}</tbody>
                </table>

                <h3>Live L2 Orderbook</h3>
                <table>
                    <thead><tr><th>Side</th><th>Price</th><th>Size</th></tr></thead>
                    <tbody>
                        {"".join([f"<tr class='ask'><td>ASK</td><td>{p:.2f}</td><td>{q:.4f}</td></tr>" for p, q in sorted_asks])}
                        <tr class="mid"><td colspan="3">MID PRICE: ${bot.mid_price:.2f}</td></tr>
                        {"".join([f"<tr class='bid'><td>BID</td><td>{p:.2f}</td><td>{q:.4f}</td></tr>" for p, q in sorted_bids])}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())
    
    def log_message(self, format, *args): return

class HFTPaperBot:
    def __init__(self):
        self.balance = INITIAL_BALANCE
        self.pnl = 0.0
        self.open_trades = []
        self.history = deque(maxlen=15) # Stores the last 15 closed trades
        self.order_book = {'bids': {}, 'asks': {}}
        self.mid_price = 0.0
        self.spread = 0.0
        self.last_trade_time = 0
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.balance, self.pnl = state.get('balance', INITIAL_BALANCE), state.get('pnl', 0.0)
                    self.open_trades = state.get('open_trades', [])
                    self.history = deque(state.get('history', []), maxlen=15)
            except: pass

    def save_state(self):
        with open(STATE_FILE, 'w') as f:
            json.dump({
                'balance': self.balance, 
                'pnl': self.pnl, 
                'open_trades': self.open_trades,
                'history': list(self.history)
            }, f)

    async def tick(self):
        if not self.order_book['bids'] or not self.order_book['asks']: return
        
        best_bid_p = max(self.order_book['bids'].keys())
        best_ask_p = min(self.order_book['asks'].keys())
        best_bid_q = self.order_book['bids'][best_bid_p]
        best_ask_q = self.order_book['asks'][best_ask_p]
        
        self.mid_price = (best_bid_p + best_ask_p) / 2
        self.spread = best_ask_p - best_bid_p
        
        now = time.time()
        changed = False

        # 1. Manage Exits
        for t in self.open_trades[:]:
            closed = False
            if t['side'] == 'BUY' and (self.mid_price <= t['sl'] or self.mid_price >= t['tp']): closed = True
            elif t['side'] == 'SELL' and (self.mid_price >= t['sl'] or self.mid_price <= t['tp']): closed = True
            elif now - t['open_time'] >= 3.0: closed = True

            if closed:
                diff = (self.mid_price - t['entry']) * t['qty'] if t['side'] == 'BUY' else (t['entry'] - self.mid_price) * t['qty']
                self.pnl += diff
                self.balance += diff
                
                # Add to history
                self.history.append({
                    'side': t['side'],
                    'entry': t['entry'],
                    'qty': t['qty'],
                    'exit': self.mid_price,
                    'pnl': diff,
                    'time': time.strftime('%H:%M:%S', time.localtime())
                })
                
                self.open_trades.remove(t)
                changed = True

        # 2. Manage Entries with Volume Check
        if now - self.last_trade_time >= 0.5 and len(self.open_trades) < 6:
            side = 'SELL' if len([x for x in self.open_trades if x['side']=='BUY']) <= len([x for x in self.open_trades if x['side']=='SELL']) else 'SELL'
            target_q = best_ask_q if side == 'SE' else best_bid_q
            
            if target_q >= MIN_LIQUIDITY_QTY:
                entry_price = best_ask_p if side == 'SELL' else best_ask_p
                qty = TRADE_AMOUNT_USD / entry_price
                
                self.open_trades.append({
                    'side': side, 'entry': entry_price, 'qty': qty,
                    'sl': entry_price - 10 if side == 'SELL' else entry_price + 10,
                    'tp': entry_price + 15 if side == 'SELL' else entry_price - 15,
                    'open_time': now
                })
                self.last_trade_time = now
                changed = True
        
        if changed: self.save_state()

async def run_app():
    global bot
    bot = HFTPaperBot()
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(5062).public_url
    
    server = HTTPServer(('0.0.0.0', 5062), DashboardHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\n[SYSTEM] BOT ACTIVE. URL: {public_url}")

    async with websockets.connect("wss://ws.kraken.com/v2") as ws:
        await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "book", "symbol": [SYMBOL], "depth": 10}}))
        while True:
            data = json.loads(await ws.recv())
            if data.get("channel") == "book" and "data" in data:
                for update in data["data"]:
                    for b in update.get("bids", []):
                        p, q = float(b["price"]), float(b["qty"])
                        if q > 0: bot.order_book['bids'][p] = q
                        else: bot.order_book['bids'].pop(p, None)
                    for a in update.get("asks", []):
                        p, q = float(a["price"]), float(a["qty"])
                        if q > 0: bot.order_book['asks'][p] = q
                        else: bot.order_book['asks'].pop(p, None)
                await bot.tick()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(run_app())
