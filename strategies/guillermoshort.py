from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi, scale_aggressiveness
import pandas as pd
import ta
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class Guillermoshort(BaseStrategy): # Heredar de BaseStrategy
    """Estrategia Guillermoshort: Scalping bajista con ruptura de soporte + retesteo + gestión de riesgo en 1m."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        # --- Definición de parámetros por nivel de agresividad ---
        agg_levels = {
            1: {"rsi_sell": 40, "retest_range_factor": 0.0005, "min_atr": 0.5},  # Muy conservador
            2: {"rsi_sell": 43, "retest_range_factor": 0.0006, "min_atr": 0.5},
            3: {"rsi_sell": 55, "retest_range_factor": 0.0010, "min_atr": 0.3},
            4: {"rsi_sell": 49, "retest_range_factor": 0.0008, "min_atr": 0.5},
            5: {"rsi_sell": 52, "retest_range_factor": 0.0009, "min_atr": 0.5},  # Intermedio
            6: {"rsi_sell": 55, "retest_range_factor": 0.0010, "min_atr": 0.5},
            7: {"rsi_sell": 58, "retest_range_factor": 0.0011, "min_atr": 0.5},
            8: {"rsi_sell": 61, "retest_range_factor": 0.0012, "min_atr": 0.5},
            9: {"rsi_sell": 64, "retest_range_factor": 0.0013, "min_atr": 0.5},
            10: {"rsi_sell": 67, "retest_range_factor": 0.0014, "min_atr": 0.5} # Muy permisivo
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Asignación de parámetros ---
        self.ema_fast_period = config.get("ema_fast_period", 25)
        self.ema_slow_period = config.get("ema_slow_period", 70)
        self.lookback_period_for_support = config.get("lookback_period_for_support", 20)
        self.retest_range_factor = config.get("retest_range_factor", level_params["retest_range_factor"])
        self.rsi_period = config.get("rsi_period", 14)
        self.volume_lookback = config.get("volume_lookback", 5)
        self.volume_multiplier = config.get("volume_multiplier", 1.2)
        self.atr_period = config.get("atr_period", 14)
        self.min_atr = config.get("min_atr", level_params["min_atr"])
        self.sl_multiplier = config.get("sl_multiplier", 1.5) # Añadido
        self.tp_multiplier = config.get("tp_multiplier", 1.0) # Añadido

        # --- Parámetros ajustados por agresividad ---
        self.rsi_sell_threshold = config.get("rsi_sell_threshold", level_params["rsi_sell"])

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = {
            "data_ok": False,
            "trend_ok": False,
            "volatility_ok": False,
            "support_level_ok": False,
            "breakout_ok": False,
            "retest_ok": False,
            "rsi_ok": False,
            "volume_ok": False,
            "macd_bearish_ok": False, # NEW
            "current_price": 0.0,
            "ema_fast_val": 0.0,
            "ema_slow_val": 0.0,
            "atr_val": 0.0,
            "support_level": 0.0,
            "rsi_val": 0.0,
            "volume_val": 0.0,
            "volume_avg_val": 0.0,
            "macd_val": 0.0, # NEW
            "macd_signal_val": 0.0, # NEW
            "error": ""
        }
        try:
            limit = max(
                self.ema_slow_period,
                self.lookback_period_for_support,
                self.rsi_period,
                self.volume_lookback,
                self.atr_period
            ) + 30

            prices = binance_data_provider.get_historical_klines(
                symbol, "1m", limit=limit
            ).get("prices", [])
            df = normalize_klines(prices, min_length=limit - 10)
            if df.empty:
                detailed_status["data_ok"] = False
                detailed_status["error"] = "Datos insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}

            # Indicadores
            df = add_ema(df, self.ema_fast_period)
            df = add_ema(df, self.ema_slow_period)
            df = add_rsi(df, self.rsi_period)
            df["volume_avg"] = df["volume"].rolling(window=self.volume_lookback).mean()
            df["ATR"] = ta.volatility.AverageTrueRange(
                high=df["high"], low=df["low"], close=df["close"], window=self.atr_period
            ).average_true_range()
            
            # NEW MACD Calculation
            df["MACD"] = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9).macd()
            df["MACD_Signal"] = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9).macd_signal()


            latest = df.iloc[-1]
            detailed_status["current_price"] = latest['close']
            detailed_status["ema_fast_val"] = latest[f'EMA{self.ema_fast_period}']
            detailed_status["ema_slow_val"] = latest[f'EMA{self.ema_slow_period}']
            detailed_status["atr_val"] = latest['ATR']
            detailed_status["rsi_val"] = latest['RSI']
            detailed_status["volume_val"] = latest['volume']
            detailed_status["volume_avg_val"] = latest['volume_avg']
            detailed_status["macd_val"] = latest["MACD"]
            detailed_status["macd_signal_val"] = latest["MACD_Signal"]


            # Filtro tendencia bajista
            if latest[f"EMA{self.ema_fast_period}"] >= latest[f"EMA{self.ema_slow_period}"] * 1.005: # Relajado
                detailed_status["trend_ok"] = False
                detailed_status["error"] = f"Tendencia no bajista (EMA{self.ema_fast_period} >= EMA{self.ema_slow_period})"
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}

            # Filtro volatilidad
            if latest["ATR"] < self.min_atr:
                detailed_status["volatility_ok"] = False
                detailed_status["error"] = f"Volatilidad insuficiente (ATR={latest['ATR']:.2f})"
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}

            # Soporte dinámico
            support_level = df['low'].iloc[-self.lookback_period_for_support-1:-1].min()
            if pd.isna(support_level):
                support_level = latest['close'] # Asignar un valor por defecto si es NaN
                detailed_status["support_level_ok"] = False
                detailed_status["error"] = "Soporte no disponible."
                detailed_status["support_level"] = support_level # Actualizar detailed_status
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["support_level"] = support_level # Actualizar detailed_status

            # Ruptura
            breakout_candles = df[df["close"] < support_level]
            if breakout_candles.empty:
                detailed_status["breakout_ok"] = False
                detailed_status["error"] = f"Esperando ruptura del soporte {support_level:.2f}"
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}

            # Retesteo + confirmación RSI/volumen
            retest_upper = support_level * (1 + self.retest_range_factor)
            retest_lower = support_level * (1 - self.retest_range_factor)
            is_rsi_ok = latest["RSI"] < self.rsi_sell_threshold
            is_volume_strong = latest["volume"] > latest["volume_avg"] * self.volume_multiplier
            
            # NEW MACD Filter
            is_macd_bearish = latest["MACD"] < latest["MACD_Signal"]
            if not is_macd_bearish:
                detailed_status["macd_bearish_ok"] = False
                detailed_status["error"] = f"MACD no bajista (MACD={latest['MACD']:.2f}, Signal={latest['MACD_Signal']:.2f})."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["macd_bearish_ok"] = True


            # Calcular SL y TP basados en ATR
            sl_pct = 0.0
            tp_pct = 0.0
            if not df.empty and not pd.isna(latest["ATR"]): # Usar ATR de 1m para SL/TP
                sl_pct = (self.sl_multiplier * latest["ATR"] / latest['close']) * 100
                tp_pct = (self.tp_multiplier * latest["ATR"] / latest['close']) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            retest_conditions_met = 0
            if retest_lower <= latest["close"] <= retest_upper: retest_conditions_met += 1
            if is_rsi_ok: retest_conditions_met += 1
            if is_volume_strong: retest_conditions_met += 1
            if is_macd_bearish: retest_conditions_met += 1

            if retest_conditions_met >= 4: # Add is_macd_bearish
                entry = latest["close"]

                return {
                    "signal": "SELL",
                    "message": f"Retesteo bajista exitoso {support_level:.2f}",
                    "entry": entry,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # Construir mensaje detallado para el estado HOLD
            hold_message_parts = []
            hold_message_parts.append(f"Estado: HOLD")
            hold_message_parts.append(f"Precio actual: {detailed_status['current_price']:.2f}")

            # Tendencia OK
            hold_message_parts.append(f"Tendencia Bajista (EMA{self.ema_fast_period} {detailed_status['ema_fast_val']:.2f} < EMA{self.ema_slow_period} {detailed_status['ema_slow_val']:.2f}): {'✅' if detailed_status['trend_ok'] else '❌'}")

            # Volatilidad OK
            hold_message_parts.append(f"Volatilidad Suficiente (ATR {detailed_status['atr_val']:.2f} >= Min ATR {self.min_atr:.2f}): {'✅' if detailed_status['volatility_ok'] else '❌'}")

            # Nivel de Soporte
            hold_message_parts.append(f"Nivel de Soporte: {detailed_status['support_level']:.2f}")

            # Ruptura OK
            hold_message_parts.append(f"Ruptura de Soporte (Precio {detailed_status['current_price']:.2f} < Soporte {detailed_status['support_level']:.2f}): {'✅' if detailed_status['breakout_ok'] else '❌'}")

            # Retesteo OK
            hold_message_parts.append(f"Retesteo (Precio {detailed_status['current_price']:.2f} entre {retest_lower:.2f} y {retest_upper:.2f}): {'✅' if detailed_status['retest_ok'] else '❌'}")

            # RSI OK
            hold_message_parts.append(f"RSI Bajista (RSI {detailed_status['rsi_val']:.2f} < Umbral {self.rsi_sell_threshold:.2f}): {'✅' if detailed_status['rsi_ok'] else '❌'}")

            # Volumen OK
            volume_expected_threshold = detailed_status['volume_avg_val'] * self.volume_multiplier
            hold_message_parts.append(f"Volumen Fuerte (Actual {detailed_status['volume_val']:.2f} > Esperado {volume_expected_threshold:.2f}): {'✅' if detailed_status['volume_ok'] else '❌'}")

            # MACD Bajista OK
            hold_message_parts.append(f"MACD Bajista (MACD {detailed_status['macd_val']:.2f} < Signal {detailed_status['macd_signal_val']:.2f}): {'✅' if detailed_status['macd_bearish_ok'] else '❌'}")

            final_hold_message = " | ".join(hold_message_parts)

            return {"signal": "HOLD", "message": final_hold_message, "detailed_status": detailed_status}

        except Exception as e:
            logger.error(f"Guillermoshort error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
