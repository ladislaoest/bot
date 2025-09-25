from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class LadisLongLite(BaseStrategy): # Heredar de BaseStrategy
    """Versión Lite de LadisLong: pullback a EMA + RSI saludable, condiciones flexibles y SL/TP dinámico por ATR."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        # --- Parámetros por agresividad ---
        agg_levels = {
            1: {"rsi_min": 35, "rsi_max": 95, "required_conditions": 10, "min_atr": 0.3, "volume_multiplier": 0.8, "min_ema_spread_pct": 0.05},
            2: {"rsi_min": 30, "rsi_max": 90, "required_conditions": 9, "min_atr": 0.2, "volume_multiplier": 0.7, "min_ema_spread_pct": 0.04},
            3: {"rsi_min": 20, "rsi_max": 90, "required_conditions": 7, "min_atr": 0.05, "volume_multiplier": 0.5, "min_ema_spread_pct": 0.02},
            4: {"rsi_min": 20, "rsi_max": 80, "required_conditions": 8, "min_atr": 0.05, "volume_multiplier": 0.5, "min_ema_spread_pct": 0.02},
            5: {"rsi_min": 15, "rsi_max": 75, "required_conditions": 8, "min_atr": 0.01, "volume_multiplier": 0.4, "min_ema_spread_pct": 0.01},
            6: {"rsi_min": 10, "rsi_max": 70, "required_conditions": 7, "min_atr": 0.01, "volume_multiplier": 0.3, "min_ema_spread_pct": 0.01},
            7: {"rsi_min": 5, "rsi_max": 65, "required_conditions": 7, "min_atr": 0.01, "volume_multiplier": 0.2, "min_ema_spread_pct": 0.01},
            8: {"rsi_min": 0, "rsi_max": 60, "required_conditions": 6, "min_atr": 0.01, "volume_multiplier": 0.1, "min_ema_spread_pct": 0.01},
            9: {"rsi_min": 0, "rsi_max": 55, "required_conditions": 6, "min_atr": 0.01, "volume_multiplier": 0.05, "min_ema_spread_pct": 0.01},
            10: {"rsi_min": 0, "rsi_max": 50, "required_conditions": 6, "min_atr": 0.01, "volume_multiplier": 0.01, "min_ema_spread_pct": 0.01}
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Configuración ---
        self.ema_slow = config.get("ema_slow", 26)
        self.ema_fast = config.get("ema_fast", 12)
        self.ema_long_trend_period = config.get("ema_long_trend_period", 50)
        self.rsi_period = config.get("rsi_period", 7)
        self.volume_lookback = config.get("volume_lookback", 5)
        self.atr_period = config.get("atr_period", 14)
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 20)
        self.min_atr = config.get("min_atr", level_params["min_atr"])
        self.volume_multiplier = config.get("volume_multiplier", level_params["volume_multiplier"])
        self.min_ema_spread_pct = config.get("min_ema_spread_pct", level_params["min_ema_spread_pct"])
        self.sl_multiplier = config.get("sl_multiplier", 1.0) # Reducido para evitar errores de SL máximo
        self.tp_multiplier = config.get("tp_multiplier", 0.8) # Añadido

        self.rsi_min_level = config.get("rsi_min_level", level_params["rsi_min"])
        self.rsi_max_level = config.get("rsi_max_level", level_params["rsi_max"])
        self.required_conditions = config.get("required_conditions", level_params["required_conditions"]) # Nuevo
        self.atr_sma_period = config.get("atr_sma_period", 20)

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = { "error": "" }
        sl_pct = 0.0 # Inicializar
        tp_pct = 0.0 # Inicializar
        try:
            # --- Datos 5m ---
            limit_5m = max(self.ema_slow, self.atr_period, self.ema_long_trend_period, self.adx_period) + 50 # Update limit_5m
            prices_5m = binance_data_provider.get_historical_klines(symbol, "5m", limit=limit_5m).get("prices", [])
            df_5m = normalize_klines(prices_5m, min_length=limit_5m - 10)
            if df_5m.empty:
                return {"signal": "HOLD", "message": f"Datos 5m insuficientes para {symbol}. Se requieren {limit_5m} velas.", "detailed_status": detailed_status}

            # --- Datos 1m ---
            limit_1m = max(self.ema_fast, self.rsi_period, self.volume_lookback) + 51
            prices_1m = binance_data_provider.get_historical_klines(symbol, "1m", limit=limit_1m).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit_1m - 10)
            if df_1m.empty:
                return {"signal": "HOLD", "message": f"Datos 1m insuficientes para {symbol}. Se requieren {limit_1m} velas.", "detailed_status": detailed_status}

            # --- Indicadores 5m ---
            df_5m = add_ema(df_5m, self.ema_slow)
            df_5m = add_ema(df_5m, self.ema_fast)
            df_5m = add_ema(df_5m, self.ema_long_trend_period)
            df_5m["ATR"] = ta.volatility.AverageTrueRange(
                high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.atr_period
            ).average_true_range()
            df_5m["ATR_SMA"] = df_5m["ATR"].rolling(window=self.atr_sma_period).mean()
            adx_indicator = ta.trend.ADXIndicator(high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.adx_period)
            df_5m['ADX'] = adx_indicator.adx()


            # --- Indicadores 1m ---
            df_1m = add_ema(df_1m, self.ema_fast)
            df_1m = add_ema(df_1m, self.ema_slow)
            df_1m = add_rsi(df_1m, self.rsi_period)
            df_1m["volume_avg"] = df_1m["volume"].rolling(window=self.volume_lookback).mean()
            macd = ta.trend.MACD(df_1m['close'])
            df_1m['MACD'] = macd.macd()
            df_1m['MACD_Signal'] = macd.macd_signal()

            # --- Últimas velas ---
            latest_5m = df_5m.iloc[-1]
            prev_5m = df_5m.iloc[-2] # Para la pendiente de la EMA
            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]

            # --- Condiciones ---
            cond_volatility = not pd.isna(latest_5m["ATR"]) and latest_5m["ATR"] >= self.min_atr
            cond_uptrend = latest_5m[f"EMA{self.ema_fast}"] > latest_5m[f"EMA{self.ema_slow}"]
            cond_longtrend = latest_5m["close"] > latest_5m[f"EMA{self.ema_long_trend_period}"]
            
            # Pullback a EMA (1m) con confirmación de vela siguiente
            cond_pullback = (
                prev_1m["low"] <= prev_1m[f"EMA{self.ema_fast}"] # La vela previa toca o cruza la EMA
                and latest_1m["close"] > latest_1m[f"EMA{self.ema_fast}"] # La vela actual cierra por encima de la EMA
            )
            
            cond_rsi = self.rsi_min_level < latest_1m["RSI"] < self.rsi_max_level
            cond_volume = latest_1m["volume"] > latest_1m["volume_avg"] * self.volume_multiplier
            cond_macd = latest_1m["MACD"] > latest_1m["MACD_Signal"]
            cond_atr_sma = latest_5m["ATR"] > latest_5m["ATR_SMA"]
            cond_adx = latest_5m["ADX"] > self.adx_threshold
            
            # NUEVO: Filtro de Mercado Lateral (Choppiness)
            ema_spread_pct = abs(latest_5m[f"EMA{self.ema_fast}"] - latest_5m[f"EMA{self.ema_slow}"]) / latest_5m['close'] * 100
            cond_choppiness = ema_spread_pct > self.min_ema_spread_pct
            
            # NUEVO: Filtro de Pendiente de Tendencia Larga
            cond_long_trend_slope = latest_5m[f"EMA{self.ema_long_trend_period}"] > prev_5m[f"EMA{self.ema_long_trend_period}"]

            # Guardar estados
            detailed_status.update({
                "ATR": latest_5m["ATR"], "ATR_SMA": latest_5m["ATR_SMA"],
                "RSI": latest_1m["RSI"], "MACD": latest_1m["MACD"], "MACD_Signal": latest_1m["MACD_Signal"], 
                "volume": latest_1m["volume"], "volume_avg": latest_1m["volume_avg"],
                "cond_volatility": cond_volatility, "cond_uptrend": cond_uptrend,
                "cond_longtrend": cond_longtrend, "cond_pullback": cond_pullback,
                "cond_rsi": cond_rsi, "cond_volume": cond_volume, "cond_macd": cond_macd,
                "cond_atr_sma": cond_atr_sma,
                "cond_adx": cond_adx,
                "cond_choppiness": cond_choppiness, # Nuevo
                "cond_long_trend_slope": cond_long_trend_slope, # Nuevo
                "ema_spread_pct": ema_spread_pct, # Nuevo
                "required_conditions": self.required_conditions # Nuevo
            })

            # --- Entrada flexible ---
            conditions = [
                cond_volatility, cond_uptrend, cond_longtrend, cond_pullback, 
                cond_rsi, cond_volume, cond_macd, cond_atr_sma,
                cond_choppiness, cond_long_trend_slope, cond_adx
            ]
            conditions_met = sum(conditions)
            
            # Calcular SL y TP basados en ATR
            if not df_5m.empty and not pd.isna(latest_5m["ATR"]): # Usar ATR de 5m para SL/TP
                sl_pct = (self.sl_multiplier * latest_5m["ATR"] / latest_1m['close']) # Usar latest_1m['close'] para el cálculo del porcentaje
                tp_pct = (self.tp_multiplier * latest_5m["ATR"] / latest_1m['close'])
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            if conditions_met >= self.required_conditions:
                entry = latest_1m["close"]
                return {
                    "signal": "BUY",
                    "message": f"Pullback confirmado con {conditions_met}/{len(conditions)} señales (Req: {self.required_conditions}).",
                    "entry": entry,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            return {
                "signal": "HOLD",
                "message": f"Condiciones insuficientes ({conditions_met}/{len(conditions)}, Req: {self.required_conditions}). " +
                           f"Volatilidad (ATR {detailed_status['ATR']:.2f} >= Min ATR {self.min_atr:.2f}): {'✅' if cond_volatility else '❌'} | " +
                           f"Tendencia Alcista (EMA{self.ema_fast} > EMA{self.ema_slow}): {'✅' if cond_uptrend else '❌'} | " +
                           f"Tendencia Larga (Close > EMA{self.ema_long_trend_period}): {'✅' if cond_longtrend else '❌'} | " +
                           f"Pullback (Vela previa roja, vela actual verde, ambas cerca de EMA{self.ema_fast}): {'✅' if cond_pullback else '❌'} | " +
                           f"RSI Saludable ({self.rsi_min_level} < RSI {detailed_status['RSI']:.2f} < {self.rsi_max_level}): {'✅' if cond_rsi else '❌'} | " +
                           f"Volumen Fuerte (Actual {detailed_status['volume']:.2f} > Promedio {detailed_status['volume_avg']:.2f} * {self.volume_multiplier}): {'✅' if cond_volume else '❌'} | " +
                           f"MACD Alcista (MACD {detailed_status['MACD']:.2f} > Signal {detailed_status['MACD_Signal']:.2f}): {'✅' if cond_macd else '❌'} | " +
                           f"ATR > ATR_SMA (ATR {detailed_status['ATR']:.2f} > ATR_SMA {detailed_status['ATR_SMA']:.2f}): {'✅' if cond_atr_sma else '❌'} | " +
                           f"ADX > Threshold (ADX {latest_5m['ADX']:.2f} > {self.adx_threshold}): {'✅' if cond_adx else '❌'} | " +
                           f"No Choppy (EMA Spread {detailed_status['ema_spread_pct']:.2f}% > {self.min_ema_spread_pct:.2f}%): {'✅' if cond_choppiness else '❌'} | " +
                           f"Pendiente Tendencia Larga (EMA{self.ema_long_trend_period} actual > previa): {'✅' if cond_long_trend_slope else '❌'}",
                "detailed_status": detailed_status
            }

        except Exception as e:
            logger.error(f"LadisLongLite error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
