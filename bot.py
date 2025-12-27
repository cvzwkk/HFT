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
TRADE_AMOUNT_USD = 500.0  # Constant USD amount per trade entry

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
            <title>HFT Bot Dashboard</title>
            <meta http-equiv="refresh" content="1">
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0b0e11; color: #eaecef; padding: 20px; }}
                .stat-container {{ display: flex; gap: 15px; margin-bottom: 20px; }}
                .stat-box {{ background: #1e2329; padding: 15px; border-radius: 10px; border: 1px solid #333; flex: 1; }}
                table {{ width: 100%; border-collapse: collapse; background: #1e2329; border-radius: 8px; overflow: hidden; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #2b2f36; }}
                th {{ background: #2b2f36; color: #848e9c; font-size: 12px; }}
                .pnl-pos {{ color: #02c076; }} .pnl-neg {{ color: #cf304a; }}
            </style>
        </head>
        <body>
            <h2 style="color:#f0b90b;">⚡ HFT Bot Live: {SYMBOL}</h2>
            <div class="stat-container">
                <div class="stat-box">Balance: <strong>${bot.balance:,.2f}</strong></div>
                <div class="stat-box">PnL: <span class="{'pnl-pos' if bot.pnl >= 0 else 'pnl-neg'}">${bot.pnl:,.2f}</span></div>
                <div class="stat-box">Active: <strong>{len(bot.open_trades)}</strong></div>
            </div>
            <h3>Active Trades</h3>
            <table>
                <thead><tr><th>Side</th><th>Entry Price</th><th>Quantity</th><th>USD Value</th><th>SL</th><th>TP</th><th>Age</th></tr></thead>
                <tbody>{trades_rows}</tbody>
            </table>
            <h3>Closed Trade History (Exit Logs)</h3>
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

    async def tick(self):
        best_bid = max(self.order_book['bids'].keys()) if self.order_book['bids'] else None
        best_ask = min(self.order_book['asks'].keys()) if self.order_book['asks'] else None
        if not best_bid or not best_ask: return
        
        now, mid = time.time(), (best_bid + best_ask) / 2
        
        # Check Exits
        for t in self.open_trades[:]:
            closed = False
            reason = ""
            if now - t['open_time'] >= 1.0: 
                closed, reason = True, "TIME"
            elif t['side'] == 'BUY' and (mid <= t['sl'] or mid >= t['tp']): 
                closed, reason = True, "SL/TP"
            elif t['side'] == 'SELL' and (mid >= t['sl'] or mid <= t['tp']): 
                closed, reason = True, "SL/TP"

            if closed:
                diff = (mid - t['entry']) * t['qty'] if t['side'] == 'BUY' else (t['entry'] - mid) * t['qty']
                self.pnl += diff
                self.balance += diff
                # Log detailed Exit Info
                self.history.append(
                    f"[{reason}] {t['side']} {t['qty']:.4f} | Entry: {t['entry']:.1f} | Exit: {mid:.1f} | PnL: {diff:.2f}"
                )
                self.open_trades.remove(t)

        # Execute Entry
        if now - self.last_trade_time >= 0.05 and len(self.open_trades) < 20:
            side = 'BUY' if len([x for x in self.open_trades if x['side']=='BUY']) < 10 else 'SELL'
            price = best_bid if side == 'BUY' else best_ask
            qty = TRADE_AMOUNT_USD / price # Amount used logic
            
            self.open_trades.append({
                'side': side, 
                'entry': price, 
                'qty': qty, 
                'usd_value': TRADE_AMOUNT_USD,
                'sl': price-12, 
                'tp': price+20, 
                'open_time': now
            })
            self.last_trade_time = now

async def run_app():
    global bot
    bot = HFTPaperBot()
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(5000).public_url
    
    server = HTTPServer(('0.0.0.0', 5000), DashboardHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\n[VPS] Dashboard: {public_url}")

    async with websockets.connect("wss://api-pub.bitfinex.com/ws/2") as ws:
        await ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": SYMBOL, "prec": "P0"}))
        while True:
            try:
                msg = await ws.recv()
                data = json.loads(msg)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    content = data[1]
                    if not isinstance(content[0], list):
                        p, c, a = content
                        side = 'bids' if a > 0 else 'asks'
                        if c > 0: bot.order_book[side][p] = abs(a)
                        else: bot.order_book[side].pop(p, None)
                        await bot.tick()
                        sys.stdout.write(f"\rBAL: ${bot.balance:,.2f} | PNL: ${bot.pnl:,.2f}   ")
                        sys.stdout.flush()
            except: continue

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(run_app())
