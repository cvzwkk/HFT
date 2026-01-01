
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
NGROK_TOKEN = "36xkALQDnxGLwLU3o1CIo2SKsvt_7cUEHiQnMbNC2Snv5bfKk"
SYMBOL = "BTC/USD" # Kraken V2 format
INITIAL_BALANCE = 100000.0
TRADE_AMOUNT_USD = 500.0
STATE_FILE = "bot_state_kraken.json"

bot = None

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        now = time.time()
        trades_rows = "".join([
            f"<tr><td>{t['side']}</td><td>{t['entry']:.1f}</td><td>{t['qty']:.4f}</td><td>${t['usd_value']:.2f}</td><td>{t['sl']:.1f}</td><td>{t['tp']:.1f}</td><td>{now - t['open_time']:.2f}s</td></tr>"
            for t in bot.open_trades
        ])

        history_list = "".join([f"<li>{log}</li>" for log in list(bot.history)[-15:]])

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Kraken HFT Dashboard</title>
            <meta http-equiv="refresh" content="1">
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #06080a; color: #eaecef; padding: 20px; }}
                .stat-container {{ display: flex; gap: 15px; margin-bottom: 20px; }}
                .stat-box {{ background: #111417; padding: 15px; border-radius: 10px; border: 1px solid #23282d; flex: 1; }}
                table {{ width: 100%; border-collapse: collapse; background: #111417; border-radius: 8px; overflow: hidden; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #23282d; }}
                th {{ background: #1e2329; color: #848e9c; font-size: 12px; }}
                .pnl-pos {{ color: #05ca7e; }} .pnl-neg {{ color: #e02424; }}
            </style>
        </head>
        <body>
            <h2 style="color:#05ca7e;">⚡ Kraken HFT Bot Live: {SYMBOL}</h2>
            <div class="stat-container">
                <div class="stat-box">Balance: <strong>${bot.balance:,.2f}</strong></div>
                <div class="stat-box">PnL: <span class="{'pnl-pos' if bot.pnl >= 0 else 'pnl-neg'}">${bot.pnl:,.2f}</span></div>
                <div class="stat-box">Active Trades: <strong>{len(bot.open_trades)}</strong></div>
            </div>
            <h3>Active Positions</h3>
            <table>
                <thead><tr><th>Side</th><th>Entry</th><th>Qty</th><th>Value</th><th>SL</th><th>TP</th><th>Age</th></tr></thead>
                <tbody>{trades_rows}</tbody>
            </table>
            <h3>Recent History</h3>
            <ul style="font-family: monospace; font-size: 13px; color: #848e9c;">{history_list}</ul>
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
        self.history = deque(maxlen=30)
        self.order_book = {'bids': {}, 'asks': {}}
        self.last_trade_time = 0
        self.load_state()

    def save_state(self):
        state = {
            'balance': self.balance,
            'pnl': self.pnl,
            'open_trades': self.open_trades,
            'history': list(self.history)
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.balance = state.get('balance', INITIAL_BALANCE)
                    self.pnl = state.get('pnl', 0.0)
                    self.open_trades = state.get('open_trades', [])
                    self.history = deque(state.get('history', []), maxlen=30)
            except: pass

    async def tick(self):
        best_bid = max(self.order_book['bids'].keys()) if self.order_book['bids'] else None
        best_ask = min(self.order_book['asks'].keys()) if self.order_book['asks'] else None
        if not best_bid or not best_ask: return
        
        now, mid = time.time(), (best_bid + best_ask) / 2
        
        # Check Exits
        changed = False
        for t in self.open_trades[:]:
            closed = False
            reason = ""
            if now - t['open_time'] >= 1.5: # Slightly longer TTL for Kraken
                closed, reason = True, "TIME"
            elif t['side'] == 'BUY' and (mid <= t['sl'] or mid >= t['tp']): 
                closed, reason = True, "SL/TP"
            elif t['side'] == 'SELL' and (mid >= t['sl'] or mid <= t['tp']): 
                closed, reason = True, "SL/TP"

            if closed:
                diff = (mid - t['entry']) * t['qty'] if t['side'] == 'BUY' else (t['entry'] - mid) * t['qty']
                self.pnl += diff
                self.balance += diff
                self.history.append(f"[{reason}] {t['side']} | Entry: {t['entry']:.1f} | Exit: {mid:.1f} | PnL: {diff:.2f}")
                self.open_trades.remove(t)
                changed = True

        # Execute Entry (0.1s delay between trades)
        if now - self.last_trade_time >= 0.1 and len(self.open_trades) < 15:
            side = 'SELL' if len([x for x in self.open_trades if x['side']=='BUY']) < 7 else 'SELL'
            price = best_bid if side == 'SELL' else best_bid
            qty = TRADE_AMOUNT_USD / price 
            
            self.open_trades.append({
                'side': side, 'entry': price, 'qty': qty, 
                'usd_value': TRADE_AMOUNT_USD,
                'sl': price-10, 'tp': price+15, 'open_time': now
            })
            self.last_trade_time = now
            changed = True
        
        if changed: self.save_state()

async def run_app():
    global bot
    bot = HFTPaperBot()
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(5000).public_url
    
    server = HTTPServer(('0.0.0.0', 5000), DashboardHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\n[SYSTEM] Dashboard: {public_url}")

    async with websockets.connect("wss://ws.kraken.com/v2") as ws:
        # Kraken V2 Subscribe to Book (depth 10 is enough for HFT mid-price)
        sub_msg = {
            "method": "subscribe",
            "params": {
                "channel": "book",
                "symbol": [SYMBOL],
                "depth": 10
            }
        }
        await ws.send(json.dumps(sub_msg))
        
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)
                
                if data.get("channel") == "book" and "data" in data:
                    # Update order book from Kraken V2 Snapshot/Update
                    for update in data["data"]:
                        for b in update.get("bids", []):
                            price, qty = b["price"], b["qty"]
                            if qty > 0: bot.order_book['bids'][price] = qty
                            else: bot.order_book['bids'].pop(price, None)
                        
                        for a in update.get("asks", []):
                            price, qty = a["price"], a["qty"]
                            if qty > 0: bot.order_book['asks'][price] = qty
                            else: bot.order_book['asks'].pop(price, None)
                    
                    await bot.tick()
                    sys.stdout.write(f"\rKRAKEN BAL: ${bot.balance:,.2f} | PNL: ${bot.pnl:,.2f}   ")
                    sys.stdout.flush()
            except Exception as e:
                continue

if __name__ == "__main__":
    nest_asyncio.apply()
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping Kraken Bot...")
