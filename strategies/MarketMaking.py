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
SYMBOL = "tBTCUSD"
INITIAL_BALANCE = 100000.0
TRADE_AMOUNT_USD = 1000.0 # Increased for better visibility
STATE_FILE = "bot_state.json"
MAX_SKEW = 5 # Max difference between Buy and Sell counts

bot = None

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/data':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            # Calculate inventory skew
            buys = len([t for t in bot.open_trades if t['side'] == 'BUY'])
            sells = len([t for t in bot.open_trades if t['side'] == 'SELL'])

            data = {
                "balance": f"{bot.balance:,.2f}",
                "pnl": f"{bot.pnl:,.2f}",
                "price": f"{bot.last_price:.2f}",
                "skew": f"B: {buys} | S: {sells}",
                "pnl_class": "pnl-pos" if bot.pnl >= 0 else "pnl-neg",
                "trades_html": "".join([
                    f"<tr><td>{t['side']}</td><td>{t['entry']:.1f}</td><td>{t['qty']:.4f}</td><td>{t['tp']:.1f}</td><td>{time.time() - t['open_time']:.1f}s</td></tr>"
                    for t in bot.open_trades
                ]),
                "history_html": "".join([f"<li>{log}</li>" for log in list(bot.history)[-15:]])
            }
            self.wfile.write(json.dumps(data).encode())
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>HFT Market Maker</title>
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0b0e11; color: #eaecef; padding: 20px; }}
                .stat-container {{ display: flex; gap: 15px; margin-bottom: 20px; }}
                .stat-box {{ background: #1e2329; padding: 15px; border-radius: 10px; border: 1px solid #333; flex: 1; }}
                table {{ width: 100%; border-collapse: collapse; background: #1e2329; border-radius: 8px; overflow: hidden; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #2b2f36; }}
                th {{ background: #2b2f36; color: #848e9c; font-size: 12px; }}
                .pnl-pos {{ color: #02c076; }} .pnl-neg {{ color: #cf304a; }}
            </style>
            <script>
                async function updateData() {{
                    try {{
                        const response = await fetch('/data');
                        const data = await response.json();
                        document.getElementById('balance').innerText = '$' + data.balance;
                        document.getElementById('pnl').innerText = '$' + data.pnl;
                        document.getElementById('pnl').className = data.pnl_class;
                        document.getElementById('price').innerText = data.price;
                        document.getElementById('skew').innerText = data.skew;
                        document.getElementById('trades-body').innerHTML = data.trades_html;
                        document.getElementById('history-list').innerHTML = data.history_html;
                    }} catch (e) {{}}
                }}
                setInterval(updateData, 400);
            </script>
        </head>
        <body>
            <h2 style="color:#f0b90b;">⚡ HFT Market Maker: {SYMBOL}</h2>
            <div class="stat-container">
                <div class="stat-box">Balance: <strong id="balance">...</strong></div>
                <div class="stat-box">Total PnL: <span id="pnl">...</span></div>
                <div class="stat-box">Best Mid: <strong id="price">...</strong></div>
                <div class="stat-box">Skew (B|S): <strong id="skew">...</strong></div>
            </div>
            <h3>Live Market Maker Positions</h3>
            <table>
                <thead><tr><th>Side</th><th>Entry (Limit)</th><th>Qty</th><th>Target (Spread)</th><th>Age</th></tr></thead>
                <tbody id="trades-body"></tbody>
            </table>
            <h3>Execution History</h3>
            <ul id="history-list" style="font-family: monospace; font-size: 11px; color: #848e9c; list-style: none; padding: 0;"></ul>
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
        self.last_price = 0.0
        self.load_state()

    def save_state(self):
        state = {'balance': self.balance, 'pnl': self.pnl, 'open_trades': self.open_trades, 'history': list(self.history)}
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

    async def tick(self, bid, ask):
        self.last_price = (bid + ask) / 2
        now = time.time()
        changed = False
        
        # 1. Check Exit conditions (Simulating Limit Fills)
        for t in self.open_trades[:]:
            closed = False
            # Exit if Target reached (Market moved across spread)
            if t['side'] == 'BUY' and ask >= t['tp']: closed = True
            elif t['side'] == 'SELL' and bid <= t['tp']: closed = True
            # Stop loss if price moves too far against us
            elif t['side'] == 'BUY' and bid < t['sl']: closed = True
            elif t['side'] == 'SELL' and ask > t['sl']: closed = True
            # Time decay (HFT positions shouldn't last long)
            elif now - t['open_time'] > 10: closed = True

            if closed:
                exit_price = ask if t['side'] == 'BUY' else bid
                diff = (exit_price - t['entry']) * t['qty'] if t['side'] == 'BUY' else (t['entry'] - exit_price) * t['qty']
                self.pnl += diff
                self.balance += diff
                self.history.append(f"FILLING {t['side']} @ {exit_price:.1f} | Net: {diff:+.2f}")
                self.open_trades.remove(t)
                changed = True

        # 2. Market Making Strategy (Providing Liquidity)
        if len(self.open_trades) < 40:
            num_buys = len([x for x in self.open_trades if x['side'] == 'BUY'])
            num_sells = len([x for x in self.open_trades if x['side'] == 'SELL'])
            
            # Place BUY at best bid if we aren't too "Long"
            if num_buys - num_sells < MAX_SKEW:
                self.open_trades.append({
                    'side': 'BUY', 'entry': bid, 'qty': TRADE_AMOUNT_USD / bid,
                    'tp': ask, 'sl': bid - 15, 'open_time': now
                })
                changed = True

            # Place SELL at best ask if we aren't too "Short"
            if num_sells - num_buys < MAX_SKEW:
                self.open_trades.append({
                    'side': 'SELL', 'entry': ask, 'qty': TRADE_AMOUNT_USD / ask,
                    'tp': bid, 'sl': ask + 15, 'open_time': now
                })
                changed = True
        
        if changed: self.save_state()

async def run_app():
    global bot
    bot = HFTPaperBot()
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(5000).public_url
    
    server = HTTPServer(('0.0.0.0', 5000), DashboardHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\n[SYSTEM] DASHBOARD: {public_url}")

    async with websockets.connect("wss://api-pub.bitfinex.com/ws/2") as ws:
        await ws.send(json.dumps({"event": "subscribe", "channel": "ticker", "symbol": SYMBOL}))
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    ticker = data[1]
                    # ticker[0] = Bid, ticker[2] = Ask
                    await bot.tick(ticker[0], ticker[2])
                    sys.stdout.write(f"\rBID: {ticker[0]:.1f} | ASK: {ticker[2]:.1f} | PNL: ${bot.pnl:,.2f} ")
                    sys.stdout.flush()
            except: continue

if __name__ == "__main__":
    nest_asyncio.apply()
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopped.")
