from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class SheilashortLite(BaseStrategy): # Heredar de BaseStrategy
    """Estrategia SheilashortLite: Scalping de venta en tendencia bajista con pullback a EMA y confirmación de RSI/MACD."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        agg_levels = {
            1: {"rsi_sell_max": 60, "required_conditions": 4, "min_atr_value": 0.8, "volume_multiplier": 1.2},
            2: {"rsi_sell_max": 63, "required_conditions": 4, "min_atr_value": 0.7, "volume_multiplier": 1.1},
            3: {"rsi_sell_max": 70, "required_conditions": 3, "min_atr_value": 0.4, "volume_multiplier": 0.8},
            4: {"rsi_sell_max": 69, "required_conditions": 3, "min_atr_value": 0.5, "volume_multiplier": 0.9},
            5: {"rsi_sell_max": 72, "required_conditions": 3, "min_atr_value": 0.4, "volume_multiplier": 0.8},
            6: {"rsi_sell_max": 75, "required_conditions": 3, "min_atr_value": 0.3, "volume_multiplier": 0.7},
            7: {"rsi_sell_max": 78, "required_conditions": 2, "min_atr_value": 0.2, "volume_multiplier": 0.6},
            8: {"rsi_sell_max": 81, "required_conditions": 2, "min_atr_value": 0.1, "volume_multiplier": 0.5},
            9: {"rsi_sell_max": 84, "required_conditions": 2, "min_atr_value": 0.05, "volume_multiplier": 0.4},
            10: {"rsi_sell_max": 87, "required_conditions": 2, "min_atr_value": 0.01, "volume_multiplier": 0.3}
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        self.ema_slow_period = config.get("ema_slow_period", 50)
        self.ema_fast_period = config.get("ema_fast_period", 20)
        self.ema_trigger_period = config.get("ema_trigger_period", 9)
        self.rsi_period = config.get("rsi_period", 14)
        self.atr_period = config.get("atr_period", 14)
        self.min_atr_value = config.get("min_atr_value", level_params["min_atr_value"])
        self.volume_window = config.get("volume_window", 20)
        self.volume_multiplier = config.get("volume_multiplier", level_params["volume_multiplier"])
        self.volume_ema_period = config.get("volume_ema_period", 20)
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 15)

        self.sl_multiplier = config.get("sl_multiplier", 1.5)
        self.tp_multiplier = config.get("tp_multiplier", 1.0)

        self.rsi_sell_max = config.get("rsi_sell_max", level_params.get("rsi_sell_max", 65))
        self.required_conditions = config.get("required_conditions", level_params["required_conditions"])

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = {
            "is_downtrend_5m": False,
            "is_pullback_5m": False,
            "volume_confirm_5m": False,
            "adx_ok": False,
            "cond_candle_1m": False,
            "cond_ema_cross_1m": False,
            "cond_macd_1m": False,
            "cond_rsi_1m": False,
            "sl_pct": 0.0,
            "tp_pct": 0.0,
            "error": "",
        }
        try:
            # --- 1. OBTENER DATOS ---
            limit_5m = max(self.ema_slow_period, self.volume_ema_period, self.adx_period) + 10
            prices_5m = binance_data_provider.get_historical_klines(symbol, "5m", limit=limit_5m).get("prices", [])
            df_5m = normalize_klines(prices_5m, min_length=limit_5m -5)
            if df_5m.empty:
                detailed_status["error"] = "Datos 5m insuficientes."
                return {"signal": "HOLD", "message": f"Datos 5m insuficientes para {symbol}.", "detailed_status": detailed_status}

            limit_1m = max(self.rsi_period, self.atr_period) + 50
            prices_1m = binance_data_provider.get_historical_klines(symbol, "1m", limit=limit_1m).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit_1m - 5)
            if df_1m.empty:
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": f"Datos 1m insuficientes para {symbol}.", "detailed_status": detailed_status}

            # --- 2. CALCULAR INDICADORES ---
            # Contexto en 5m
            df_5m = add_ema(df_5m, self.ema_slow_period)
            df_5m = add_ema(df_5m, self.ema_fast_period)
            df_5m['Volume_EMA'] = ta.trend.ema_indicator(df_5m['volume'], window=self.volume_ema_period, fillna=True)
            df_5m['ADX'] = ta.trend.ADXIndicator(high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.adx_period).adx()
            latest_5m = df_5m.iloc[-1]

            # Gatillo en 1m
            df_1m = add_ema(df_1m, self.ema_trigger_period)
            df_1m = add_rsi(df_1m, self.rsi_period)
            macd_1m = ta.trend.MACD(df_1m['close'])
            df_1m['MACD'] = macd_1m.macd()
            df_1m['MACD_Signal'] = macd_1m.macd_signal()
            df_1m["ATR"] = ta.volatility.AverageTrueRange(high=df_1m["high"], low=df_1m["low"], close=df_1m["close"], window=self.atr_period).average_true_range()
            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]

            # --- 3. VERIFICAR CONTEXTO (5m) ---
            is_downtrend = latest_5m[f'EMA{self.ema_fast_period}'] < latest_5m[f'EMA{self.ema_slow_period}'] and latest_5m['close'] < latest_5m[f'EMA{self.ema_slow_period}']
            is_pullback = latest_5m[f'EMA{self.ema_fast_period}'] < latest_5m['close'] < latest_5m[f'EMA{self.ema_slow_period}']
            volume_confirm_5m = latest_5m['volume'] > latest_5m['Volume_EMA'] * self.volume_multiplier
            # adx_ok = latest_5m['ADX'] > self.adx_threshold # Opcional

            detailed_status.update({
                "is_downtrend_5m": is_downtrend,
                "is_pullback_5m": is_pullback,
                "volume_confirm_5m": volume_confirm_5m,
                # "adx_ok": adx_ok,
            })

            if not (is_downtrend and is_pullback and volume_confirm_5m):
                return {"signal": "HOLD", "message": "Esperando contexto bajista (tendencia + pullback + volumen) en 5m.", "detailed_status": detailed_status}

            # --- 4. BUSCAR GATILLO (1m) ---
            cond_candle = latest_1m['close'] < latest_1m['open']
            cond_ema_cross = latest_1m['close'] < latest_1m[f'EMA{self.ema_trigger_period}']
            cond_rsi = latest_1m['RSI'] < self.rsi_sell_max and latest_1m['RSI'] < prev_1m['RSI']
            cond_macd = latest_1m['MACD'] < latest_1m['MACD_Signal']

            detailed_status.update({
                "cond_candle_1m": cond_candle,
                "cond_ema_cross_1m": cond_ema_cross,
                "cond_rsi_1m": cond_rsi,
                "cond_macd_1m": cond_macd,
            })

            # --- 5. GESTIÓN DE RIESGO Y SEÑAL ---
            sl_pct = 0.0
            tp_pct = 0.0
            if not pd.isna(latest_1m["ATR"]):
                sl_pct = (self.sl_multiplier * latest_1m["ATR"] / latest_1m['close']) * 100
                tp_pct = (self.tp_multiplier * latest_1m["ATR"] / latest_1m['close']) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            trigger_conditions = [cond_candle, cond_ema_cross, cond_macd, cond_rsi]
            if sum(trigger_conditions) >= self.required_conditions:
                entry = latest_1m['close']
                return {
                    "signal": "SELL",
                    "message": f"Gatillo de venta detectado con {sum(trigger_conditions)}/{len(trigger_conditions)} condiciones.",
                    "entry": entry,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            return {"signal": "HOLD", "message": "Contexto OK, esperando gatillo de entrada en 1m.", "detailed_status": detailed_status}

        except Exception as e:
            logger.error(f"SheilashortLite error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}