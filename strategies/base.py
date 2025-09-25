import logging
from strategies.utils import normalize_strategy_result

logger = logging.getLogger(__name__)

class BaseStrategy:
    def __init__(self, config=None, aggressiveness_level=3):
        self.config = config or {}
        self.aggressiveness_level = aggressiveness_level

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        raise NotImplementedError("El método 'run' debe ser implementado por las subclases.")

    def safe_run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        try:
            out = self.run(capital_client_api, binance_data_provider, symbol)
        except Exception as e:
            logger.exception(f"Error en run() de la estrategia {self.__class__.__name__}")
            out = {
                "signal": "ERROR",
                "message": str(e),
                "detailed_status": {"error": str(e)}
            }
        return normalize_strategy_result(out)
