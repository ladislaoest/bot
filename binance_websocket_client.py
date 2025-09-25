
import asyncio
import websockets
import json
import logging

logger = logging.getLogger(__name__)

class BinanceWebsocketClient:
    def __init__(self, symbol, interval, data_queue):
        self.symbol = symbol.lower()
        self.interval = interval
        self.data_queue = data_queue
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@kline_{self.interval}"
        self.running = False

    async def _run(self):
        self.running = True
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    logger.info(f"Conectado al WebSocket de Binance para {self.symbol}@{self.interval}")
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=60.0)
                            data = json.loads(message)
                            if 'k' in data:
                                kline = data['k']
                                if kline['x']:  # Si la vela está cerrada
                                    self.data_queue.put(kline)
                        except asyncio.TimeoutError:
                            logger.warning("Timeout esperando mensaje de WebSocket. Intentando reconectar...")
                            break  # Salir del bucle interno para reconectar
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning("Conexión WebSocket cerrada. Intentando reconectar...")
                            break
            except Exception as e:
                logger.error(f"Error en el cliente WebSocket de Binance: {e}")
                await asyncio.sleep(5)

    def start(self):
        import threading
        threading.Thread(target=self._start_event_loop, daemon=True).start()

    def _start_event_loop(self):
        asyncio.run(self._run())

    def stop(self):
        self.running = False
