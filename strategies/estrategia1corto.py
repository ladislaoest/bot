# En strategies/estrategia1corto.py
from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta
import logging # Añadido
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__) # Añadido

class estrategia1corto(BaseStrategy): # Heredar de BaseStrategy
    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        agg_levels = {
            1: {"rsi_sell": 65, "required_conditions": 4, "adx_threshold": 15},
            2: {"rsi_sell": 62, "required_conditions": 4, "adx_threshold": 15},
            3: {"rsi_sell": 60, "required_conditions": 3, "adx_threshold": 12},
            4: {"rsi_sell": 51, "required_conditions": 4, "adx_threshold": 15},
            5: {"rsi_sell": 48, "required_conditions": 4, "adx_threshold": 15},
            6: {"rsi_sell": 45, "required_conditions": 4, "adx_threshold": 15},
            7: {"rsi_sell": 42, "required_conditions": 4, "adx_threshold": 15},
            8: {"rsi_sell": 39, "required_conditions": 4, "adx_threshold": 15},
            9: {"rsi_sell": 36, "required_conditions": 4, "adx_threshold": 15},
            10: {"rsi_sell": 33, "required_conditions": 4, "adx_threshold": 15}
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        self.ema9_period = config.get('ema9_period', 9)
        self.ema20_period = config.get('ema20_period', 20)
        self.ema50_period = config.get('ema50_period', 50)
        self.rsi_period = config.get('rsi_period', 14)
        self.atr_period = config.get("atr_period", 14) # Nuevo
        self.sl_multiplier = config.get("sl_multiplier", 1.5) # Nuevo
        self.tp_multiplier = config.get("tp_multiplier", 1.0) # Nuevo
        self.adx_period = config.get("adx_period", 14) # Nuevo
        self.adx_threshold = config.get("adx_threshold", level_params["adx_threshold"]) # Nuevo
        
        self.rsi_sell_threshold = config.get('rsi_sell_threshold', level_params["rsi_sell"])
        self.required_conditions = config.get('required_conditions', level_params["required_conditions"])

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = {
            "cond_downtrend_5m": False,
            "cond_pullback_5m": False,
            "cond_ema_cross_1m": False,
            "cond_macd_1m": False,
            "cond_rsi_1m": False,
            "adx_filter_ok_5m": False,
            "rsi_1m": 0.0,
            "macd_diff_1m": 0.0,
            "sl_pct": 0.0,
            "tp_pct": 0.0,
            "error": "",
            "current_close_5m": 0.0,
            "ema20_5m": 0.0,
            "ema50_5m": 0.0,
            "current_high_5m": 0.0,
            "current_atr_5m": 0.0,
            "adx_5m": 0.0,
            "adx_threshold_config": self.adx_threshold,
            "current_close_1m": 0.0,
            "ema9_1m": 0.0,
            "current_rsi_1m": 0.0,
            "prev_rsi_1m": 0.0,
            "rsi_sell_threshold_config": self.rsi_sell_threshold,
            "current_macd_diff_1m": 0.0,
            "prev_macd_diff_1m": 0.0
        }
        try:
            # --- Timeframes ---
            prices_5m = binance_data_provider.get_historical_klines("BTCUSDT", "5m", limit=max(self.ema50_period, self.atr_period, self.adx_period) + 50).get("prices", []) # Ajustar límite
            df_5m = normalize_klines(prices_5m, min_length=max(self.ema50_period, self.atr_period, self.adx_period) + 5) # Ajustar min_length
            if df_5m.empty:
                detailed_status["error"] = "Datos 5m insuficientes."
                return {"signal": "HOLD", "message": "Datos 5m insuficientes.", "detailed_status": detailed_status}

            prices_1m = binance_data_provider.get_historical_klines("BTCUSDT", "1m", limit=self.rsi_period + 50).get("prices", []) # Ajustar límite
            df_1m = normalize_klines(prices_1m, min_length=self.rsi_period + 5) # Ajustar min_length
            if df_1m.empty:
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": "Datos 1m insuficientes.", "detailed_status": detailed_status}

            # --- Indicadores (5m) ---
            df_5m = add_ema(df_5m, self.ema20_period)
            df_5m = add_ema(df_5m, self.ema50_period)
            df_5m["ATR"] = ta.volatility.AverageTrueRange(
                high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.atr_period
            ).average_true_range() # Nuevo
            df_5m["ADX"] = ta.trend.ADXIndicator(
                high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.adx_period
            ).adx() # Nuevo
            
            # --- Indicadores (1m) ---
            df_1m = add_ema(df_1m, self.ema9_period)
            df_1m = add_rsi(df_1m, self.rsi_period)
            macd_1m = ta.trend.MACD(df_1m['close'])
            df_1m['MACD'] = macd_1m.macd()
            df_1m['MACD_Signal'] = macd_1m.macd_signal()
            df_1m['MACD_Diff'] = macd_1m.macd_diff()

            latest_5m = df_5m.iloc[-1]
            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]

            detailed_status["current_close_5m"] = latest_5m['close']
            detailed_status["ema20_5m"] = latest_5m[f'EMA{self.ema20_period}']
            detailed_status["ema50_5m"] = latest_5m[f'EMA{self.ema50_period}']
            detailed_status["current_high_5m"] = latest_5m['high']
            detailed_status["current_atr_5m"] = latest_5m['ATR']
            detailed_status["adx_5m"] = latest_5m['ADX']

            detailed_status["current_close_1m"] = latest_1m['close']
            detailed_status["ema9_1m"] = latest_1m[f'EMA{self.ema9_period}']
            detailed_status["current_rsi_1m"] = latest_1m['RSI']
            detailed_status["prev_rsi_1m"] = prev_1m['RSI']
            detailed_status["current_macd_diff_1m"] = latest_1m['MACD_Diff']
            detailed_status["prev_macd_diff_1m"] = prev_1m['MACD_Diff']

            # Calcular SL y TP basados en ATR
            sl_pct = 0.0
            tp_pct = 0.0
            if not pd.isna(latest_5m["ATR"]):
                sl_pct = (self.sl_multiplier * latest_5m["ATR"] / latest_5m['close']) * 100
                tp_pct = (self.tp_multiplier * latest_5m["ATR"] / latest_5m['close']) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            # --- Condiciones de Contexto (5m) ---
            # 1. Tendencia General Bajista
            cond_downtrend = latest_5m['close'] <= latest_5m[f'EMA{self.ema50_period}'] * 1.005 # Relajado
            
            # 2. Pullback hacia EMA de resistencia
            cond_pullback = latest_5m['close'] >= latest_5m[f'EMA{self.ema20_period}'] * 0.995 # Relajado

            # 3. Filtro de mercado lateral (ADX)
            adx_filter_ok = not pd.isna(latest_5m["ADX"]) and latest_5m["ADX"] > self.adx_threshold # Nuevo

            # --- Condiciones de Gatillo (1m) ---
            # 1. Cruce bajista de la EMA rápida
            cond_ema_cross = latest_1m['close'] < latest_1m[f'EMA{self.ema9_period}']
            
            # 2. MACD perdiendo fuerza o ya bajista
            cond_macd = latest_1m['MACD_Diff'] < prev_1m['MACD_Diff']
            
            # 3. RSI por debajo del umbral y cayendo
            cond_rsi = latest_1m['RSI'] < self.rsi_sell_threshold and latest_1m['RSI'] < prev_1m['RSI']

            detailed_status.update({
                "cond_downtrend_5m": cond_downtrend,
                "cond_pullback_5m": cond_pullback,
                "adx_filter_ok_5m": adx_filter_ok, # Nuevo
                "cond_ema_cross_1m": cond_ema_cross,
                "cond_macd_1m": cond_macd,
                "cond_rsi_1m": cond_rsi,
                "rsi_1m": latest_1m['RSI'],
                "macd_diff_1m": latest_1m['MACD_Diff']
            })

            # --- Lógica de Entrada ---
            if cond_downtrend and cond_pullback and adx_filter_ok: # Añadir ADX al contexto
                # El contexto es bueno, ahora buscamos el gatillo en 1m
                trigger_conditions = [cond_ema_cross, cond_macd, cond_rsi]
                conditions_met = sum(trigger_conditions)
                
                if conditions_met >= self.required_conditions:
                    entry = latest_1m['close']
                    return {
                        "signal": "SELL",
                        "message": f"Gatillo de venta con {conditions_met}/{len(trigger_conditions)} condiciones en 1m. Contexto: Bajista, Pullback, ADX OK.",
                        "entry": entry,
                        "sl_pct": sl_pct,
                        "tp_pct": tp_pct,
                        "detailed_status": detailed_status
                    }
            
            # Construir mensaje detallado para el estado HOLD
            hold_message_parts = []
            
            # Resumen de condiciones de contexto
            hold_message_parts.append(f"Tendencia Bajista (5m): Precio {latest_5m['close']:.2f} < EMA{self.ema50_period} {latest_5m[f'EMA{self.ema50_period}']:.2f} {'✅' if cond_downtrend else '❌'}")
            hold_message_parts.append(f"Pullback (5m): High {latest_5m['high']:.2f} >= EMA{self.ema20_period} {latest_5m[f'EMA{self.ema20_period}']:.2f} {'✅' if cond_pullback else '❌'}")
            hold_message_parts.append(f"ADX OK (5m): ADX {latest_5m['ADX']:.2f} > {self.adx_threshold} {'✅' if adx_filter_ok else '❌'}")

            # Resumen de condiciones de gatillo
            hold_message_parts.append(f"Cruce EMA Bajista (1m): Close {latest_1m['close']:.2f} < EMA{self.ema9_period} {latest_1m[f'EMA{self.ema9_period}']:.2f} {'✅' if cond_ema_cross else '❌'}")
            hold_message_parts.append(f"MACD Bajista (1m): MACD_Diff {latest_1m['MACD_Diff']:.2f} < Prev_MACD_Diff {prev_1m['MACD_Diff']:.2f}: {'✅' if cond_macd else '❌'}")
            hold_message_parts.append(f"RSI Cayendo (1m): RSI {latest_1m['RSI']:.2f} < {self.rsi_sell_threshold} y < Prev_RSI {prev_1m['RSI']:.2f}: {'✅' if cond_rsi else '❌'}")

            final_hold_message = " | ".join(hold_message_parts)

            return {"signal": "HOLD", "message": f"Esperando condiciones: {final_hold_message}", "detailed_status": detailed_status}

        except Exception as e:
            logger.error(f"estrategia1corto error: {str(e)}")
            detailed_status["error"] = str(e) # Añadir error al detailed_status
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}