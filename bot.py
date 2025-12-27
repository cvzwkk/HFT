import asyncio
import json
import time
import websockets
import nest_asyncio
import threading
import sys
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyngrok import ngrok

# --- CONFIGURATION ---
NGROK_TOKEN = "36xkALQDnxGLwLU3o1CIo2SKsvt_7cUEHiQnMbNC2Snv5bfKk"
SYMBOL = "tBTCUSD"
INITIAL_BALANCE = 100000.0

# Global bot reference for the HTTP server
bot = None

# --- NATIVE HTTP SERVER HANDLER ---
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        now = time.time()
        trades_rows = "".join([
            f"<tr><td>{t['side']}</td><td>{t['entry']:.1f}</td><td>{t['sl']:.1f}</td><td>{t['tp']:.1f}</td><td>{now - t['open_time']:.2f}s</td></tr>"
            for t in bot.open_trades
        ])

        history_list = "".join([f"<li>{log}</li>" for log in list(bot.history)[-10:]])

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>HFT Bot Dashboard</title>
            <meta http-equiv="refresh" content="1">
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0b0e11; color: #eaecef; padding: 30px; }}
                .stat-box {{ display: inline-block; background: #1e2329; padding: 20px; border-radius: 12px; margin-right: 15px; border: 1px solid #333; min-width: 180px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 25px; background: #1e2329; }}
                th, td {{ padding: 15px; text-align: left; border-bottom: 1px solid #2b2f36; }}
                th {{ color: #848e9c; font-size: 13px; text-transform: uppercase; }}
                .pnl {{ font-size: 24px; color: {"#02c076" if bot.pnl >= 0 else "#cf304a"}; }}
                h1 {{ color: #f0b90b; }}
            </style>
        </head>
        <body>
            <h1>🚀 HFT Live Trade Monitor</h1>
            <div class="stat-box"><h3>Balance</h3><div style="font-size:24px;">${bot.balance:,.2f}</div></div>
            <div class="stat-box"><h3>Total PnL</h3><div class="pnl">${bot.pnl:,.2f}</div></div>
            <div class="stat-box"><h3>Open Trades</h3><div style="font-size:24px;">{len(bot.open_trades)}</div></div>
            
            <h3>Active Positions</h3>
            <table>
                <thead><tr><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Age</th></tr></thead>
                <tbody>{trades_rows}</tbody>
            </table>
            <h3>Log History</h3>
            <ul style="color: #848e9c; font-size: 14px;">{history_list}</ul>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def log_message(self, format, *args): return # Disable server console logs

# --- BOT ENGINE ---
class HFTPaperBot:
    def __init__(self):
        self.balance = INITIAL_BALANCE
        self.pnl = 0.0
        self.open_trades = [] 
        self.history = deque(maxlen=20)
        self.order_book = {'bids': {}, 'asks': {}}
        self.last_trade_time = 0

    async def tick(self):
        best_bid = max(self.order_book['bids'].keys()) if self.order_book['bids'] else None
        best_ask = min(self.order_book['asks'].keys()) if self.order_book['asks'] else None
        if not best_bid or not best_ask: return
        
        now, mid = time.time(), (best_bid + best_ask) / 2
        
        # Position Management
        for t in self.open_trades[:]:
            closed = False
            if now - t['open_time'] >= 1.0: closed = True
            elif t['side'] == 'BUY' and (mid <= t['sl'] or mid >= t['tp']): closed = True
            elif t['side'] == 'SELL' and (mid >= t['sl'] or mid <= t['tp']): closed = True

            if closed:
                diff = (mid - t['entry']) if t['side'] == 'BUY' else (t['entry'] - mid)
                self.pnl += diff
                self.balance += diff
                self.history.append(f"{t['side']} closed | PnL: {diff:.2f}")
                self.open_trades.remove(t)

        # Logic to open new paper trades
        if now - self.last_trade_time >= 0.05:
            if len(self.open_trades) < 30:
                side = 'BUY' if len([x for x in self.open_trades if x['side']=='BUY']) < 15 else 'SELL'
                px = best_bid if side == 'BUY' else best_ask
                self.open_trades.append({'side': side, 'entry': px, 'sl': px-10, 'tp': px+15, 'open_time': now})
                self.last_trade_time = now

async def run_app():
    global bot
    bot = HFTPaperBot()
    
    # Setup Ngrok
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(5000).public_url
    
    # Start Web Server Thread
    server = HTTPServer(('0.0.0.0', 5000), DashboardHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\n[SYSTEM] VPS Bot Started.")
    print(f"[SYSTEM] Live Dashboard: {public_url}\n")

    async with websockets.connect("wss://api-pub.bitfinex.com/ws/2") as ws:
        await ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": SYMBOL, "prec": "P0"}))
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    content = data[1]
                    if isinstance(content[0], list):
                        for e in content: bot.order_book['bids' if e[2]>0 else 'asks'][e[0]] = abs(e[2])
                    else:
                        p, c, a = content
                        side = 'bids' if a > 0 else 'asks'
                        if c > 0: bot.order_book[side][p] = abs(a)
                        else: bot.order_book[side].pop(p, None)
                    
                    await bot.tick()
                    
                    # Terminal UI (Standard Print)
                    sys.stdout.write(f"\rPNL: ${bot.pnl:,.2f} | ACTIVE: {len(bot.open_trades)}   ")
                    sys.stdout.flush()
            except Exception: continue

if __name__ == "__main__":
    nest_asyncio.apply()
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        ngrok.kill()
        print("\n[SYSTEM] Shutdown complete.")
