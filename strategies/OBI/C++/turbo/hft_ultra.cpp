#include <iostream>
#include <map>
#include <vector>
#include <string>
#include <iomanip>
#include <chrono>
#include <cmath>
#include <ixwebsocket/IXWebSocket.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

struct Trade {
    double entry;
    double size;
    std::string side;
};

class UltraTurboTrader {
public:
    double usd = 10000.0;
    double btc = 0.5;
    double order_size = 0.01;
    double stop_loss = 0.60;
    double take_profit = 0.90;
    int total_trades = 0, gains = 0, losses = 0;
    std::string status_msg = "SISTEMA INICIADO";
    
    std::map<double, double> bids, asks;
    std::vector<Trade> active_trades;
    
    // FIXED: Explicit type for class member
    std::chrono::steady_clock::time_point start_time = std::chrono::steady_clock::now();

    void update_book(double price, int count, double amount) {
        if (count > 0) {
            if (amount > 0) bids[price] = amount;
            else asks[price] = std::abs(amount);
        } else {
            bids.erase(price);
            asks.erase(price);
        }
    }

    void process_logic() {
        if (bids.empty() || asks.empty()) return;

        double best_bid = bids.rbegin()->first;
        double v_bid = bids.rbegin()->second;
        double best_ask = asks.begin()->first;
        double v_ask = asks.begin()->second;

        // Formula for Microprice and Order Book Imbalance
        double micro = (best_bid * v_ask + best_ask * v_bid) / (v_bid + v_ask);
        double imb = (v_bid - v_ask) / (v_bid + v_ask);

        // RISK CONTROLLER (Loop backwards to safe-delete trades)
        for (int i = active_trades.size() - 1; i >= 0; i--) {
            auto& t = active_trades[i];
            double current_p = (t.side == "BUY") ? best_ask : best_bid;
            double pnl = (current_p - t.entry) * (t.side == "BUY" ? 1 : -1);

            if (pnl >= take_profit || pnl <= -stop_loss) {
                if (t.side == "BUY") { usd += current_p * order_size; btc -= order_size; }
                else { usd -= current_p * order_size; btc += order_size; }
                
                total_trades++;
                if (pnl > 0) gains++; else losses++;
                active_trades.erase(active_trades.begin() + i);
            }
        }

        // ENTRY LOGIC: IMBALANCE > 0.75
        if (active_trades.size() < 100) {
            if (imb > 0.75 && usd >= (order_size * best_bid)) {
                usd -= order_size * best_bid; btc += order_size;
                active_trades.push_back({best_bid, order_size, "BUY"});
                status_msg = "EXECUTADO: BUY @ " + std::to_string(best_bid);
            } else if (imb < -0.75 && btc >= order_size) {
                usd += order_size * best_ask; btc -= order_size;
                active_trades.push_back({best_ask, order_size, "SELL"});
                status_msg = "EXECUTADO: SELL @ " + std::to_string(best_ask);
            }
        }
        render_ui(best_bid, micro, imb);
    }

    void render_ui(double price, double micro, double imb) {
        auto now = std::chrono::steady_clock::now();
        auto uptime = std::chrono::duration_cast<std::chrono::seconds>(now - start_time).count();
        double wr = (total_trades > 0) ? ((double)gains / total_trades * 100.0) : 0.0;

        std::cout << "\033[H" << std::fixed << std::setprecision(2);
        std::cout << "\033[1;37;44m HFT TURBO | UP: " << uptime << "s \033[0m" << std::string(30, ' ') << "\n";
        std::cout << " PREÇO: " << price << " | \033[1;33mMICRO: " << std::setprecision(4) << micro << "\033[0m\n";
        std::cout << " SALDO USD: $" << std::setprecision(2) << usd << " | BTC: " << btc << "\n";
        std::cout << std::string(50, '-') << "\n";
        std::cout << " TRADES: " << total_trades << " | \033[1;32mG: " << gains << "\033[0m | \033[1;31mL: " << losses << "\033[0m\n";
        std::cout << " WIN RATE: " << wr << "% | ATIVAS: " << active_trades.size() << "\n";
        std::cout << " STATUS: " << status_msg << "                                \n";
        std::cout << "\033[J"; 
    }
};

int main() {
    ix::WebSocket ws;
    UltraTurboTrader bot;

    ws.setUrl("wss://api-pub.bitfinex.com/ws/2");
    ws.setOnMessageCallback([&](const ix::WebSocketMessagePtr& msg) {
        if (msg->type == ix::WebSocketMessageType::Open) {
            ws.send("{\"event\":\"subscribe\",\"channel\":\"book\",\"symbol\":\"tBTCUSD\",\"prec\":\"P0\"}");
        } else if (msg->type == ix::WebSocketMessageType::Message) {
            try {
                auto j = json::parse(msg->str);
                if (!j.is_array() || j.size() < 2 || j[1] == "hb") return;

                if (j[1].is_array()) {
                    if (j[1][0].is_array()) { // INITIAL SNAPSHOT
                        for (auto& entry : j[1]) bot.update_book(entry[0], entry[1], entry[2]);
                    } else { // INDIVIDUAL UPDATE
                        bot.update_book(j[1][0], j[1][1], j[1][2]);
                    }
                    bot.process_logic();
                }
            } catch (...) {}
        }
    });

    ws.start();
    while (true) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    return 0;
}
