import asyncio
import json
import time
import websockets
import nest_asyncio
import threading
import sys
import os
import numpy as np
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyngrok import ngrok
from sklearn.neural_network import MLPClassifier

# --- CONFIGURATION ---
NGROK_TOKEN = "36xhpiAn5cRi9ObeqeKYdJBZ13k_3z1GytiAf4Sn3czxWwNBm"
SYMBOL = "tBTCUSD"
INITIAL_BALANCE = 100000.0
TRADE_AMOUNT_USD = 500.0
STATE_FILE = "bot_state.json"
MIN_DATA_POINTS = 100  # Minimum ticks before AI starts trading

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
        <html>
        <head>
            <title>AI HFT Bot Dashboard</title>
            <meta http-equiv="refresh" content="1">
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0b0e11; color: #eaecef; padding: 20px; }}
                .stat-container {{ display: flex; gap: 15px; margin-bottom: 20px; }}
                .stat-box {{ background: #1e2329; padding: 15px; border-radius: 10px; border: 1px solid #333; flex: 1; }}
                table {{ width: 100%; border-collapse: collapse; background: #1e2329; border-radius: 8px; overflow: hidden; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #2b2f36; }}
                th {{ background: #2b2f36; color: #848e9c; font-size: 12px; }}
                .pnl-pos {{ color: #02c076; }} .pnl-neg {{ color: #cf304a; }}
                .ai-status {{ color: #f0b90b; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h2 style="color:#f0b90b;">🤖 AI HFT Bot Live: {SYMBOL}</h2>
            <div class="stat-container">
                <div class="stat-box">Balance: <strong>${bot.balance:,.2f}</strong></div>
                <div class="stat-box">AI Status: <span class="ai-status">{'READY' if bot.model_ready else f'TRAINING ({len(bot.feature_buffer)}/{MIN_DATA_POINTS})'}</span></div>
                <div class="stat-box">Active Trades: <strong>{len(bot.open_trades)}</strong></div>
            </div>
            <h3>Active Trades</h3>
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
        
        # --- AI Strategy Components ---
        self.model = MLPClassifier(hidden_layer_sizes=(10, 5), max_iter=500)
        self.model_ready = False
        self.feature_buffer = deque(maxlen=500)
        self.label_buffer = deque(maxlen=500)
        self.last_mid_price = None
        
        self.load_state()

    def get_orderbook_imbalance(self):
        bid_vol = sum(self.order_book['bids'].values())
        ask_vol = sum(self.order_book['asks'].values())
        if (bid_vol + ask_vol) == 0: return 0
        return (bid_vol - ask_vol) / (bid_vol + ask_vol)

    def train_model(self):
        if len(self.feature_buffer) < MIN_DATA_POINTS: return
        X = np.array(list(self.feature_buffer)).reshape(-1, 1)
        y = np.array(list(self.label_buffer))
        
        # Only train if we have both classes (up and down)
        if len(np.unique(y)) > 1:
            self.model.fit(X, y)
            self.model_ready = True

    def save_state(self):
        state = {'balance': self.balance, 'pnl': self.pnl, 'open_trades': self.open_trades}
        with open(STATE_FILE, 'w') as f: json.dump(state, f)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.balance = state.get('balance', INITIAL_BALANCE)
                    self.pnl = state.get('pnl', 0.0)
                    self.open_trades = state.get('open_trades', [])
            except: pass

    async def tick(self):
        best_bid = max(self.order_book['bids'].keys()) if self.order_book['bids'] else None
        best_ask = min(self.order_book['asks'].keys()) if self.order_book['asks'] else None
        if not best_bid or not best_ask: return
        
        now, mid = time.time(), (best_bid + best_ask) / 2
        imbalance = self.get_orderbook_imbalance()

        # 1. DATA COLLECTION (For training the NN)
        if self.last_mid_price is not None:
            # Label: 1 if price went up, 0 if down/same
            label = 1 if mid > self.last_mid_price else 0
            self.feature_buffer.append(imbalance)
            self.label_buffer.append(label)
            
            # Re-train every 50 new data points
            if len(self.feature_buffer) % 50 == 0:
                self.train_model()
        
        self.last_mid_price = mid

        # 2. CHECK EXITS
        changed = False
        for t in self.open_trades[:]:
            closed = False
            if now - t['open_time'] >= 2.0: closed = True # Increased time slightly for NN
            elif t['side'] == 'BUY' and (mid <= t['sl'] or mid >= t['tp']): closed = True
            elif t['side'] == 'SELL' and (mid >= t['sl'] or mid <= t['tp']): closed = True

            if closed:
                diff = (mid - t['entry']) * t['qty'] if t['side'] == 'BUY' else (t['entry'] - mid) * t['qty']
                self.pnl += diff
                self.balance += diff
                self.history.append(f"AI {t['side']} Exit | PnL: ${diff:.2f}")
                self.open_trades.remove(t)
                changed = True

        # 3. AI-BASED ENTRY
        if self.model_ready and now - self.last_trade_time >= 0.2 and len(self.open_trades) < 15:
            # Predict move based on current imbalance
            prediction = self.model.predict([[imbalance]])[0]
            side = 'BUY' if prediction == 1 else 'SELL'
            
            price = best_bid if side == 'BUY' else best_ask
            qty = TRADE_AMOUNT_USD / price 
            
            self.open_trades.append({
                'side': side, 'entry': price, 'qty': qty, 
                'usd_value': TRADE_AMOUNT_USD,
                'sl': price - 15, 'tp': price + 25, 'open_time': now
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
    print(f"\n[AI BOT] Dashboard: {public_url}")

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
                        status = "READY" if bot.model_ready else "COLLECTING"
                        sys.stdout.write(f"\rAI: {status} | BAL: ${bot.balance:,.2f} | PNL: ${bot.pnl:,.2f}   ")
                        sys.stdout.flush()
            except Exception: continue

if __name__ == "__main__":
    nest_asyncio.apply()
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping bot...")
