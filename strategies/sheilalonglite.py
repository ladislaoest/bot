from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class SheilalongLite(BaseStrategy): # Heredar de BaseStrategy # Version check: Added comment
    """Estrategia SheilalongLite: Scalping de compra en tendencia alcista con pullback a EMA y confirmación de RSI/MACD."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        # --- Parámetros por nivel de agresividad ---
        agg_levels = {
            1: {"rsi_buy": 55},  # Conservador
            2: {"rsi_buy": 52},
            3: {"rsi_buy": 45},
            4: {"rsi_buy": 46},
            5: {"rsi_buy": 43},  # Intermedio
            6: {"rsi_buy": 40},
            7: {"rsi_buy": 37},
            8: {"rsi_buy": 34},
            9: {"rsi_buy": 31},
            10: {"rsi_buy": 28} # Permisivo
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Configuración ---
        self.opening_range_minutes = config.get("opening_range_minutes", 7)
        self.ema_fast = config.get("ema_fast", 9)
        self.ema_slow = config.get("ema_slow", 15)
        self.ema_trend_period = config.get("ema_trend_period", 40)
        self.rsi_period = config.get("rsi_period", 14)
        self.volume_lookback = config.get("volume_lookback", 10)
        self.volume_multiplier = config.get("volume_multiplier", 0.8)
        self.atr_period = config.get("atr_period", 14)
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 15)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.atr_sma_period = config.get("atr_sma_period", 20) # New parameter for ATR average
        self.sl_multiplier = config.get("sl_multiplier", 1.2)
        self.tp_multiplier = config.get("tp_multiplier", 1.0)

        # Umbral RSI dinámico
        self.rsi_buy_threshold = config.get("rsi_buy_threshold", level_params["rsi_buy"])

    def run(self, capital_client_api, trading_bot_instance, symbol="BTCUSDT"):
        sl_pct = 0.0
        tp_pct = 0.0
        detailed_status = {
            "opening_range_high": 0.0,
            "valid_breakout": False,
            "rsi_ok": False,
            "volume_strong": False,
            "macd_ok": False,
            "atr_ok": False,
            "adx_ok": False,
            "error": "",
            "sl_pct": sl_pct,
            "tp_pct": tp_pct
        }
        try:
            # --- Datos 1m ---
            limit_1m = max(self.opening_range_minutes, self.ema_slow, self.rsi_period, self.volume_lookback, self.macd_slow, self.atr_period, self.adx_period) + 30
            prices_1m = trading_bot_instance._get_binance_klines_data(symbol, "1m", limit=limit_1m).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit_1m - 10)
            if df_1m.empty:
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": "Datos 1m insuficientes.", "detailed_status": detailed_status}

            df_1m = add_ema(df_1m, self.ema_fast)
            df_1m = add_ema(df_1m, self.ema_slow)
            df_1m = add_rsi(df_1m, self.rsi_period)
            df_1m["volume_avg"] = df_1m["volume"].rolling(window=self.volume_lookback).mean()
            df_1m["MACD"] = ta.trend.macd(df_1m["close"], window_fast=self.macd_fast, window_slow=self.macd_slow)
            df_1m["MACD_Signal"] = ta.trend.macd_signal(df_1m["close"], window_fast=self.macd_fast, window_slow=self.macd_slow, window_sign=self.macd_signal)
            df_1m["ATR"] = ta.volatility.AverageTrueRange(high=df_1m["high"], low=df_1m["low"], close=df_1m["close"], window=self.atr_period).average_true_range()
            adx_indicator = ta.trend.ADXIndicator(high=df_1m["high"], low=df_1m["low"], close=df_1m["close"], window=self.adx_period)
            df_1m['ADX'] = adx_indicator.adx()

            # --- Datos 5m ---
            limit_5m = max(self.ema_slow, self.ema_trend_period, self.atr_period, self.atr_sma_period) + 30
            prices_5m = trading_bot_instance._get_binance_klines_data(symbol, "5m", limit=limit_5m).get("prices", [])
            df_5m = normalize_klines(prices_5m, min_length=limit_5m - 10)
            if df_5m.empty:
                detailed_status["error"] = f"Datos 5m insuficientes para {symbol}."
                return {"signal": "HOLD", "message": f"Datos 5m insuficientes para {symbol}.", "detailed_status": detailed_status}

            df_5m = add_ema(df_5m, self.ema_fast)
            df_5m = add_ema(df_5m, self.ema_slow)
            df_5m = add_ema(df_5m, self.ema_trend_period)
            df_5m["ATR"] = ta.volatility.AverageTrueRange(high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.atr_period).average_true_range()
            df_5m["ATR_SMA"] = df_5m["ATR"].rolling(window=self.atr_sma_period).mean()

            # --- Últimos datos ---
            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]
            latest_5m = df_5m.iloc[-1]

            # --- Opening range ---
            opening_range_high = df_1m["high"].iloc[:self.opening_range_minutes].max()
            detailed_status["opening_range_high"] = opening_range_high

            # --- Condiciones ---
            valid_breakout = latest_1m["close"] > opening_range_high and prev_1m["close"] > opening_range_high
            is_volume_strong = (latest_1m["volume"] > latest_1m["volume_avg"] * self.volume_multiplier)
            is_rsi_ok = latest_1m["RSI"] > self.rsi_buy_threshold
            macd_ok = latest_1m["MACD"] > latest_1m["MACD_Signal"] and prev_1m["MACD"] < prev_1m["MACD_Signal"]
            atr_ok = latest_5m["ATR"] > latest_5m["ATR_SMA"]
            adx_ok = latest_1m["ADX"] > self.adx_threshold

            detailed_status.update({
                "valid_breakout": valid_breakout,
                "rsi_ok": is_rsi_ok,
                "volume_strong": is_volume_strong,
                "macd_ok": macd_ok,
                "atr_ok": atr_ok,
                "adx_ok": adx_ok
            })

            # --- Lógica de entrada ---
            if valid_breakout and is_volume_strong:
                optional_conditions = [is_rsi_ok, macd_ok, atr_ok, adx_ok]
                if sum(optional_conditions) >= 2:
                    entry = latest_1m["close"]

                    # --- Gestión de Riesgo ---
                    if not pd.isna(latest_1m["ATR"]) and latest_1m["ATR"] > 0 and latest_1m['close'] > 0:
                        sl_pct = (self.sl_multiplier * latest_1m["ATR"] / latest_1m['close'])
                        tp_pct = (self.tp_multiplier * latest_1m["ATR"] / latest_1m['close'])
                    else:
                        # Si ATR o close son inválidos, usar valores por defecto o de configuración
                        sl_pct = self.sl_multiplier * 0.01 # Un valor pequeño por defecto
                        tp_pct = self.tp_multiplier * 0.01 # Un valor pequeño por defecto
                        detailed_status["error"] = "ATR o precio de cierre inválido para SL/TP."
                    detailed_status["sl_pct"] = sl_pct
                    detailed_status["tp_pct"] = tp_pct

                    detailed_status.update({
                        "sl_pct": sl_pct,
                        "tp_pct": tp_pct
                    })
                    return {
                        "signal": "BUY",
                        "message": f"Ruptura alcista confirmada con {sum(optional_conditions) + 2}/6 condiciones.",
                        "entry": entry,
                        "sl_pct": sl_pct,
                        "tp_pct": tp_pct,
                        "detailed_status": detailed_status
                    }

            return {
                "signal": "HOLD",
                "message": f"Esperando condiciones suficientes. Breakout: {valid_breakout}, Volumen: {is_volume_strong}, Opcionales: {sum([is_rsi_ok, macd_ok, atr_ok, adx_ok])}/4.",
                "detailed_status": detailed_status
            }

        except Exception as e:
            logger.error(f"SheilalongLite error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
