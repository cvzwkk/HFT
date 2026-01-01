<template>
  <div id="app">
    <div class="system-monitor">
      <div class="monitor-header">
        <div class="status-indicator">
          <span class="dot" :class="{ 'is-online': isConnected }"></span>
          {{ isConnected ? 'LIVE: BINANCE.US' : 'RECONNECTING...' }}
        </div>
        <div class="symbol-tag">BTC / USDT</div>
      </div>
      <div class="log-container" ref="logScroll">
        <div v-for="(log, index) in logs" :key="index" :class="['log-entry', log.type]">
          <span class="timestamp">[{{ log.time }}]</span> {{ log.text }}
        </div>
      </div>
    </div>

    <div class="chart-container">
      <trading-vue 
        ref="tradingVue"
        :data="chartData" 
        :width="width" 
        :height="height"
        :color-back="'#0c0e12'"
        :color-grid="'#1e222d'"
        :color-text="'#707a8a'">
      </trading-vue>
    </div>
  </div>
</template>

<script>
import TradingVue from 'trading-vue-js'

export default {
  name: 'App',
  components: { TradingVue },
  data() {
    return {
      chartData: { ohlcv: [] },
      logs: [],
      width: window.innerWidth,
      height: window.innerHeight - 130, // Adjust for monitor height
      isConnected: false,
      socket: null,
      symbol: 'BTCUSDT',
      interval: '1m'
    }
  },
  async mounted() {
    window.addEventListener('resize', this.onResize);
    
    // 1. First, get historical context
    await this.fetchInitialData();
    
    // 2. Then, start the live engine
    this.initWebSocket();
  },
  beforeDestroy() {
    window.removeEventListener('resize', this.onResize);
    if (this.socket) this.socket.close();
  },
  methods: {
    addLog(text, type = 'info') {
      const time = new Date().toLocaleTimeString();
      this.logs.push({ text, type, time });
      this.$nextTick(() => {
        const el = this.$refs.logScroll;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    onResize() {
      this.width = window.innerWidth;
      this.height = window.innerHeight - 130;
    },

    async fetchInitialData() {
      this.addLog("Fetching last 100 candles from REST API...");
      try {
        const url = `https://api.binance.us/api/v3/klines?symbol=${this.symbol}&interval=${this.interval}&limit=100`;
        const response = await fetch(url);
        const data = await response.json();

        // Convert Binance array format to TradingVue format
        // [Time, Open, High, Low, Close, Volume]
        this.chartData.ohlcv = data.map(c => [
          c[0],               // Open Time
          parseFloat(c[1]),    // Open
          parseFloat(c[2]),    // High
          parseFloat(c[3]),    // Low
          parseFloat(c[4]),    // Close
          parseFloat(c[5])     // Volume
        ]);

        this.addLog(`Success: ${data.length} candles loaded.`, 'success');
      } catch (err) {
        this.addLog("History Fetch Failed: " + err.message, "error");
      }
    },

    initWebSocket() {
      const streamName = `${this.symbol.toLowerCase()}@kline_${this.interval}`;
      const wsUrl = `wss://stream.binance.us:9443/ws/${streamName}`;
      
      this.addLog(`Opening Stream: ${streamName}...`);
      this.socket = new WebSocket(wsUrl);

      this.socket.onopen = () => {
        this.isConnected = true;
        this.addLog("Live stream active.", "success");
      };

      this.socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const k = msg.k; // Kline data
        
        // Prepare current tick
        const tick = [
          k.t,                 // Time
          parseFloat(k.o),      // Open
          parseFloat(k.h),      // High
          parseFloat(k.l),      // Low
          parseFloat(k.c),      // Close
          parseFloat(k.v)       // Volume
        ];

        this.updateLiveCandle(tick);
      };

      this.socket.onclose = () => {
        this.isConnected = false;
        this.addLog("Stream disconnected. Retrying in 5s...", "error");
        setTimeout(this.initWebSocket, 5000);
      };
    },

    updateLiveCandle(tick) {
      const ohlcv = this.chartData.ohlcv;
      const last = ohlcv[ohlcv.length - 1];

      // If tick time matches last candle time, update it.
      // Else, push a new candle to the array.
      if (last && tick[0] === last[0]) {
        this.$set(ohlcv, ohlcv.length - 1, tick);
      } else {
        ohlcv.push(tick);
        // Prune data to keep chart smooth (last 500 candles)
        if (ohlcv.length > 500) ohlcv.shift();
      }
    }
  }
}
</script>

<style>
/* Base Theme */
body { margin: 0; background: #0c0e12; color: #eaecef; font-family: 'Inter', sans-serif; overflow: hidden; }

/* Monitor Panel */
.system-monitor { height: 130px; border-bottom: 1px solid #2b3139; background: #161a1e; display: flex; flex-direction: column; }
.monitor-header { padding: 8px 15px; background: #1e2329; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2b3139; }
.symbol-tag { font-weight: bold; font-size: 14px; color: #f0b90b; }

.status-indicator { font-size: 12px; display: flex; align-items: center; font-weight: 500; }
.dot { height: 8px; width: 8px; background: #f6465d; border-radius: 50%; margin-right: 8px; transition: 0.3s; }
.dot.is-online { background: #0ecb81; box-shadow: 0 0 10px #0ecb81; }

/* Logs */
.log-container { flex-grow: 1; overflow-y: auto; padding: 10px 15px; font-family: monospace; font-size: 12px; }
.log-entry { margin-bottom: 4px; border-left: 2px solid transparent; padding-left: 8px; }
.info { color: #848e9c; }
.success { color: #0ecb81; border-color: #0ecb81; }
.error { color: #f6465d; border-color: #f6465d; }
.timestamp { color: #474d57; margin-right: 8px; }

/* Chart Container */
.chart-container { width: 100vw; }

/* Scrollbar UI */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-thumb { background: #3b424e; border-radius: 10px; }
</style>
