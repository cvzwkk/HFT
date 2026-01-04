sudo apt update
sudo apt install -y build-essential cmake git libssl-dev zlib1g-dev g++

sudo wget https://github.com/nlohmann/json/releases/download/v3.11.2/json.hpp -P /usr/local/include/nlohmann/

git clone https://github.com/machinezone/IXWebSocket.git
cd IXWebSocket
mkdir build
cd build
cmake -DUSE_TLS=ON ..
make -j$(nproc)
sudo make install
sudo ldconfig

g++ -Ofast -march=native hft_ultra.cpp -o hft_ultra \
    -lixwebsocket -lssl -lcrypto -lpthread -lz -ldl

sudo nice -n -20 ./hft_ultra
