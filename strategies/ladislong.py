from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi, scale_aggressiveness
import pandas as pd
import ta
import logging

from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

class LadisLong(BaseStrategy):
    """Estrategia LadisLong: Scalping rápido basado en pullback a EMA y RSI saludable."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level)
        config = config or {}
        self.aggressiveness_level = aggressiveness_level

        agg_levels = {
            1: {"rsi_min": 35, "rsi_max": 95, "min_atr": 0.3, "volume_multiplier": 0.8},  # Conservador
            2: {"rsi_min": 30, "rsi_max": 90, "min_atr": 0.2, "volume_multiplier": 0.7},
            3: {"rsi_min": 20, "rsi_max": 90, "min_atr": 0.05, "volume_multiplier": 0.5},
            4: {"rsi_min": 20, "rsi_max": 80, "min_atr": 0.05, "volume_multiplier": 0.5},
            5: {"rsi_min": 15, "rsi_max": 75, "min_atr": 0.01, "volume_multiplier": 0.4},  # Intermedio
            6: {"rsi_min": 10, "rsi_max": 70, "min_atr": 0.01, "volume_multiplier": 0.3},
            7: {"rsi_min": 5, "rsi_max": 65, "min_atr": 0.01, "volume_multiplier": 0.2},
            8: {"rsi_min": 0, "rsi_max": 60, "min_atr": 0.01, "volume_multiplier": 0.1},
            9: {"rsi_min": 0, "rsi_max": 55, "min_atr": 0.01, "volume_multiplier": 0.05},
            10: {"rsi_min": 0, "rsi_max": 50, "min_atr": 0.01, "volume_multiplier": 0.01} # Permisivo
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Asignación de parámetros ---
        self.ema_slow = config.get("ema_slow", 20)
        self.ema_fast = config.get("ema_fast", 9)
        self.ema_long_trend_period = config.get("ema_long_trend_period", 50)
        self.rsi_period = config.get("rsi_period", 7)
        self.volume_lookback = config.get("volume_lookback", 5)
        self.atr_period = config.get("atr_period", 14)
        self.min_atr = config.get("min_atr", level_params["min_atr"])
        self.sl_multiplier = config.get("sl_multiplier", 1.5) # Añadido
        self.tp_multiplier = config.get("tp_multiplier", 1.0) # Añadido
        self.volume_multiplier = config.get("volume_multiplier", level_params["volume_multiplier"]) # Añadido

        # --- Parámetros ajustados por agresividad ---
        self.rsi_min_level = config.get("rsi_min_level", level_params["rsi_min"])
        self.rsi_max_level = config.get("rsi_max_level", level_params["rsi_max"])

    def run(self, capital_client_api, trading_bot_instance, symbol="BTCUSDT"):
        detailed_status = {
            "data_5m_ok": False,
            "data_1m_ok": False,
            "volatility_ok": False,
            "volatility_reason": "",
            "uptrend_5m_ok": False,
            "uptrend_5m_reason": "",
            "long_trend_ok": False, # NEW
            "pullback_rebound_ok": False,
            "pullback_rebound_reason": "",
            "rsi_healthy_ok": False,
            "rsi_healthy_reason": "",
            "volume_strong_ok": False,
            "volume_strong_reason": "",
            "macd_bullish_ok": False,
            "macd_bullish_reason": "",
            "current_price": 0.0,
            "ema_fast_5m": 0.0,
            "ema_slow_5m": 0.0,
            "atr_5m": 0.0,
            "rsi_1m": 0.0,
            "macd_1m": 0.0,
            "macd_signal_1m": 0.0,
            "volume_1m": 0.0,
            "volume_avg_1m": 0.0,
            "prev_volume_1m": 0.0,
            "error": ""
        }
        try:
            # Datos 5m para tendencia y ATR
            limit_5m = max(self.ema_slow, self.atr_period, self.ema_long_trend_period) + 50 # Update limit_5m
            prices_5m = trading_bot_instance._get_binance_klines_data(symbol, "5m", limit=limit_5m).get("prices", [])
            df_5m = normalize_klines(prices_5m, min_length=limit_5m - 10)
            if df_5m.empty:
                detailed_status["data_5m_ok"] = False
                detailed_status["error"] = "Datos 5m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["data_5m_ok"] = True

            # Datos 1m para pullback y señal
            limit_1m = max(self.ema_fast, self.rsi_period, self.volume_lookback) + 50
            prices_1m = trading_bot_instance._get_binance_klines_data(symbol, "1m", limit=limit_1m).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit_1m - 10)
            if df_1m.empty:
                detailed_status["data_1m_ok"] = False
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["data_1m_ok"] = True

            # EMAs en 5m
            df_5m = add_ema(df_5m, self.ema_slow)
            df_5m = add_ema(df_5m, self.ema_fast)
            df_5m = add_ema(df_5m, self.ema_long_trend_period) # NEW EMA calculation

            # ATR en 5m
            df_5m["ATR"] = ta.volatility.AverageTrueRange(
                high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.atr_period
            ).average_true_range()

            # EMAs y RSI en 1m
            df_1m = add_ema(df_1m, self.ema_fast)
            df_1m = add_ema(df_1m, self.ema_slow)
            df_1m = add_rsi(df_1m, self.rsi_period)
            df_1m['volume_avg'] = df_1m['volume'].rolling(window=self.volume_lookback).mean()

            # MACD en 1m
            macd = ta.trend.MACD(df_1m['close'], window_slow=26, window_fast=12, window_sign=9)
            df_1m['MACD'] = macd.macd()
            df_1m['MACD_Signal'] = macd.macd_signal()

            latest_5m = df_5m.iloc[-1]
            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]

            detailed_status["current_price"] = latest_1m['close']

            # --- Filtros --- 
            # 1. Filtro de volatilidad (ATR en 5m)
            if pd.isna(latest_5m["ATR"]) or latest_5m["ATR"] < self.min_atr:
                detailed_status["volatility_ok"] = False
                detailed_status["volatility_reason"] = f"Volatilidad insuficiente (ATR={latest_5m['ATR']:.2f} < {self.min_atr})"
                return {"signal": "HOLD", "message": f"Volatilidad (ATR {latest_5m['ATR']:.2f} < Min ATR {self.min_atr:.2f}): {'✅' if detailed_status['volatility_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["volatility_ok"] = True

            # 2. Filtro de tendencia alcista fuerte en 5m (EMA fast > EMA slow)
            uptrend_5m = latest_5m[f"EMA{self.ema_fast}"] > latest_5m[f"EMA{self.ema_slow}"]
            if not uptrend_5m:
                detailed_status["uptrend_5m_ok"] = False
                detailed_status["uptrend_5m_reason"] = f"Tendencia 5m no alcista (EMA{self.ema_fast}: {latest_5m[f'EMA{self.ema_fast}']:.2f} <= EMA{self.ema_slow}: {latest_5m[f'EMA{self.ema_slow}']:.2f})"
                return {"signal": "HOLD", "message": f"Tendencia 5m (EMA{self.ema_fast}: {latest_5m[f'EMA{self.ema_fast}']:.2f} > EMA{self.ema_slow}: {latest_5m[f'EMA{self.ema_slow}']:.2f}): {'✅' if detailed_status['uptrend_5m_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["uptrend_5m_ok"] = True

            # 3. NUEVO: Filtro de tendencia a largo plazo en 5m (precio > EMA de tendencia larga)
            long_trend = latest_5m['close'] > latest_5m[f"EMA{self.ema_long_trend_period}"]
            if not long_trend:
                detailed_status["long_trend_ok"] = False
                detailed_status["error"] = f"Precio por debajo de la EMA de tendencia larga ({self.ema_long_trend_period})."
                # Construir mensaje detallado para el estado HOLD (tendencia larga)
                hold_message_parts = []
                hold_message_parts.append(f"Tendencia Larga (Precio 5m: {latest_5m['close']:.2f} > EMA{self.ema_long_trend_period}: {latest_5m[f'EMA{self.ema_long_trend_period}']:.2f}): {'✅' if detailed_status['long_trend_ok'] else '❌'}")
                final_hold_message = " | ".join(hold_message_parts)
                return {"signal": "HOLD", "message": final_hold_message, "detailed_status": detailed_status}
            detailed_status["long_trend_ok"] = True

            # 4. Condición de pullback más estricta en 1m (mecha toca EMA, cierre por encima, vela verde)
            pullback_rebound = (
                latest_1m["low"] <= latest_1m[f"EMA{self.ema_fast}"] # La vela toca o cruza la EMA
                and latest_1m["close"] > latest_1m[f"EMA{self.ema_fast}"] # La vela actual cierra por encima de la EMA
            )
            if not pullback_rebound:
                detailed_status["pullback_rebound_ok"] = False
                detailed_status["pullback_rebound_reason"] = f"Esperando pullback válido en 1m (Precio {latest_1m['close']:.2f}, EMA{self.ema_fast} {latest_1m[f'EMA{self.ema_fast}']:.2f})"
                return {"signal": "HOLD", "message": f"Pullback (Precio {latest_1m['close']:.2f}, EMA{self.ema_fast} {latest_1m[f'EMA{self.ema_fast}']:.2f}): {'✅' if detailed_status['pullback_rebound_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["pullback_rebound_ok"] = True

            # 5. Filtro RSI (evitar sobrecompra)
            is_rsi_healthy = self.rsi_min_level < latest_1m['RSI'] < self.rsi_max_level
            if not is_rsi_healthy:
                detailed_status["rsi_healthy_ok"] = False
                detailed_status["rsi_healthy_reason"] = f"RSI fuera de rango saludable ({latest_1m['RSI']:.2f}) (Min: {self.rsi_min_level}, Max: {self.rsi_max_level})"
                return {"signal": "HOLD", "message": f"RSI Saludable (RSI {latest_1m['RSI']:.2f} entre {self.rsi_min_level} y {self.rsi_max_level}): {'✅' if detailed_status['rsi_healthy_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["rsi_healthy_ok"] = True

            # 6. Filtro de volumen (mayor que promedio y mayor que vela previa)
            is_volume_strong = latest_1m['volume'] > latest_1m['volume_avg'] * self.volume_multiplier and latest_1m['volume'] > prev_1m['volume']
            if not is_volume_strong:
                detailed_status["volume_strong_ok"] = False
                detailed_status["volume_strong_reason"] = f"Volumen no fuerte (Actual: {latest_1m['volume']:.2f}, Promedio: {latest_1m['volume_avg']:.2f}, Anterior: {prev_1m['volume']:.2f})"
                return {"signal": "HOLD", "message": f"Volumen Fuerte (Actual: {latest_1m['volume']:.2f}, Promedio: {latest_1m['volume_avg']:.2f}, Anterior: {prev_1m['volume']:.2f}): {'✅' if detailed_status['volume_strong_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["volume_strong_ok"] = True

            # 7. Confirmación MACD (Cruce alcista en las últimas 3 velas)
            is_macd_bullish = any(df_1m['MACD'].iloc[-i] > df_1m['MACD_Signal'].iloc[-i] and df_1m['MACD'].iloc[-i-1] < df_1m['MACD_Signal'].iloc[-i-1] for i in range(1, 4))
            if not is_macd_bullish:
                detailed_status["macd_bullish_ok"] = False
                detailed_status["macd_bullish_reason"] = f"MACD no alcista (MACD: {latest_1m['MACD']:.2f}, Signal: {latest_1m['MACD_Signal']:.2f})"
                return {"signal": "HOLD", "message": f"MACD Alcista (MACD: {latest_1m['MACD']:.2f}, Signal: {latest_1m['MACD_Signal']:.2f}): {'✅' if detailed_status['macd_bullish_ok'] else '❌'}", "detailed_status": detailed_status}
            detailed_status["macd_bullish_ok"] = True

            # Calcular SL y TP basados en ATR
            sl_pct = 0.0
            tp_pct = 0.0
            if not df_5m.empty and not pd.isna(latest_5m["ATR"]) and latest_5m["ATR"] > 0 and latest_1m['close'] > 0: # Usar ATR de 5m para SL/TP
                sl_pct = (self.sl_multiplier * latest_5m["ATR"] / latest_1m['close'])
                tp_pct = (self.tp_multiplier * latest_5m["ATR"] / latest_1m['close'])
            else:
                # Si ATR o close son inválidos, usar valores por defecto o de configuración
                sl_pct = self.sl_multiplier * 0.01 # Un valor pequeño por defecto
                tp_pct = self.tp_multiplier * 0.01 # Un valor pequeño por defecto
                detailed_status["error"] = "ATR o precio de cierre inválido para SL/TP."
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            # --- Señal de compra válida ---
            entry = latest_1m["close"]

            return {
                "signal": "BUY",
                "message": "Pullback confirmado con filtros múltiples",
                "entry": entry,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
                "detailed_status": detailed_status
            }

        except Exception as e:
            logger.error(f"LadisLong error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
