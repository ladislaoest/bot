from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class ScalpingEmaRsi(BaseStrategy): # Heredar de BaseStrategy
    """
    Estrategia de Scalping basada en el cruce de Medias Móviles Exponenciales (EMA)
    y la confirmación del Índice de Fuerza Relativa (RSI).
    - Timeframe: 5 minutos
    - Indicadores: EMA(8), EMA(21), RSI(14)
    - Compra: Cruce alcista de EMAs con RSI por debajo de un umbral y subiendo.
    - Venta: Cruce bajista de EMAs con RSI por encima de un umbral y bajando.
    """

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        # --- Definición de parámetros por nivel de agresividad ---
        agg_levels = {
            1: {"rsi_buy_max": 45, "rsi_sell_min": 55},  # Conservador
            2: {"rsi_buy_max": 48, "rsi_sell_min": 52},
            3: {"rsi_buy_max": 55, "rsi_sell_min": 45},
            4: {"rsi_buy_max": 54, "rsi_sell_min": 46},
            5: {"rsi_buy_max": 57, "rsi_sell_min": 43},  # Intermedio
            6: {"rsi_buy_max": 60, "rsi_sell_min": 40},
            7: {"rsi_buy_max": 63, "rsi_sell_min": 37},
            8: {"rsi_buy_max": 66, "rsi_sell_min": 34},
            9: {"rsi_buy_max": 69, "rsi_sell_min": 31},
            10: {"rsi_buy_max": 72, "rsi_sell_min": 28} # Permisivo
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Asignación de parámetros ---
        self.ema_fast_period = config.get("ema_fast_period", 8)
        self.ema_slow_period = config.get("ema_slow_period", 21)
        self.rsi_period = config.get("rsi_period", 14)
        self.atr_period = config.get("atr_period", 14) # Añadido
        self.sl_multiplier = config.get("sl_multiplier", 1.5) # Añadido
        self.tp_multiplier = config.get("tp_multiplier", 1.0) # Añadido
        
        # --- Parámetros ajustados por agresividad ---
        self.rsi_buy_max = config.get("rsi_buy_max", level_params["rsi_buy_max"])
        self.rsi_sell_min = config.get("rsi_sell_min", level_params["rsi_sell_min"])

    def run(self, capital_client_api, trading_bot_instance, symbol="BTCUSDT"):
        sl_pct = 0.0 # Inicializar
        tp_pct = 0.0 # Inicializar
        detailed_status = {
            "sl_pct": sl_pct,
            "tp_pct": tp_pct
        }
        try:
            # --- Obtención de datos ---
            limit = self.ema_slow_period + self.rsi_period + 5 # Suficiente histórico para los indicadores
            prices = trading_bot_instance._get_binance_klines_data(symbol, "5m", limit=limit).get("prices", [])
            df = normalize_klines(prices, min_length=limit - 2)

            if df.empty:
                return {"signal": "HOLD", "message": f"Datos insuficientes para la estrategia {symbol}. Se requieren {limit} velas.", "detailed_status": detailed_status}
            # --- Cálculo de Indicadores ---
            df = add_ema(df, self.ema_fast_period)
            df = add_ema(df, self.ema_slow_period)
            df = add_rsi(df, self.rsi_period)
            df["ATR"] = ta.volatility.AverageTrueRange(
                high=df["high"], low=df["low"], close=df["close"], window=self.atr_period
            ).average_true_range()

            # Obtener las dos últimas velas para detectar el cruce
            latest = df.iloc[-1]
            previous = df.iloc[-2]

            ema_fast_latest = latest[f'EMA{self.ema_fast_period}']
            ema_slow_latest = latest[f'EMA{self.ema_slow_period}']
            rsi_latest = latest['RSI']
            
            ema_fast_previous = previous[f'EMA{self.ema_fast_period}']
            ema_slow_previous = previous[f'EMA{self.ema_slow_period}']
            rsi_previous = previous['RSI']

            # Calcular SL y TP basados en ATR
            if not pd.isna(latest["ATR"]):
                sl_pct = (self.sl_multiplier * latest["ATR"] / latest['close'])
                tp_pct = (self.tp_multiplier * latest["ATR"] / latest['close'])
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct
            detailed_status["ATR"] = latest["ATR"]

            # --- Lógica de Compra (Long) ---
            is_buy_crossover = ema_fast_previous < ema_slow_previous and ema_fast_latest > ema_slow_latest
            is_buy_rsi_confirm = rsi_latest < self.rsi_buy_max

            if is_buy_crossover and is_buy_rsi_confirm:
                entry_price = latest['close']
                message = f"Cruce alcista de EMAs o RSI ({rsi_latest:.2f} < {self.rsi_buy_max}) confirmado."
                detailed_status.update({
                    "ema_fast": ema_fast_latest, "ema_slow": ema_slow_latest,
                    "rsi": rsi_latest, "crossover": "BUY"
                })
                return {
                    "signal": "BUY",
                    "entry": entry_price,
                    "message": message,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # --- Lógica de Venta (Short) ---
            is_sell_crossover = ema_fast_previous > ema_slow_previous and ema_fast_latest < ema_slow_latest
            is_sell_rsi_confirm = rsi_latest > self.rsi_sell_min

            if is_sell_crossover and is_sell_rsi_confirm:
                entry_price = latest['close']
                message = f"Cruce bajista de EMAs o RSI ({rsi_latest:.2f} > {self.rsi_sell_min}) confirmado."
                detailed_status.update({
                    "ema_fast": ema_fast_latest, "ema_slow": ema_slow_latest,
                    "rsi": rsi_latest, "crossover": "SELL"
                })
                return {
                    "signal": "SELL",
                    "entry": entry_price,
                    "message": message,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # --- Sin señal ---
            return {"signal": "HOLD", "message": f"Esperando cruce de EMAs con confirmación de RSI. " +
                           f"EMA Rápida ({self.ema_fast_period}): {ema_fast_latest:.2f}, EMA Lenta ({self.ema_slow_period}): {ema_slow_latest:.2f}, RSI: {rsi_latest:.2f}. " +
                           f"Condición de Compra: EMA Rápida > EMA Lenta (anteriormente <) y RSI < {self.rsi_buy_max} y subiendo. " +
                           f"Condición de Venta: EMA Rápida < EMA Lenta (anteriormente >) y RSI > {self.rsi_sell_min} y bajando.", "sl_pct": 0.0, "tp_pct": 0.0, "detailed_status": detailed_status}

        except Exception as e:
            logger.error(f"Error en ScalpingEmaRsi: {str(e)}")
            return {"signal": "ERROR", "message": str(e)}