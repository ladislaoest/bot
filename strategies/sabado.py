from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi, scale_aggressiveness
import pandas as pd
import ta
from strategies.base import BaseStrategy # Añadido

class Sabado(BaseStrategy): # Heredar de BaseStrategy
    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual

        # --- Definición de parámetros por nivel de agresividad ---
        agg_levels = {
            1: {"rsi_ob": 50},  # Conservador
            2: {"rsi_ob": 53},
            3: {"rsi_ob": 56},
            4: {"rsi_ob": 59},
            5: {"rsi_ob": 62},  # Intermedio
            6: {"rsi_ob": 65},
            7: {"rsi_ob": 68},
            8: {"rsi_ob": 71},
            9: {"rsi_ob": 74},
            10: {"rsi_ob": 77} # Permisivo
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])

        # --- Asignación de parámetros ---
        self.ema_long_period = config.get('ema_long_period', 200)
        self.ema_medium_period = config.get('ema_medium_period', 50)
        self.ema_short_period = config.get('ema_short_period', 20)
        self.ema_slow_period = config.get('ema_slow_period', 50)
        self.rsi_period = config.get('rsi_period', 14)
        self.volume_ema_period = config.get('volume_ema_period', 20)
        self.volume_multiplier = config.get('volume_multiplier', 1.5)
        self.max_atr_threshold = config.get('max_atr_threshold', 0.01)
        self.min_atr_threshold = config.get('min_atr_threshold', 0.0005)


        # --- Parámetros ajustados por agresividad ---
        self.rsi_overbought_threshold = config.get('rsi_overbought_threshold', 70) # Ajustado a 70
        self.atr_period = config.get("atr_period", 14) # Añadido
        self.sl_multiplier = config.get("sl_multiplier", 0.8) # Añadido
        self.tp_multiplier = config.get("tp_multiplier", 1.2) # Añadido

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = {
            "data_5m_ok": False,
            "data_1m_ok": False,
            "is_downtrend": False,
            "is_pullback": False,
            "rsi_confirm": False,
            "macd_confirm": False,
            "entry_candle_confirm": False,
            "volume_confirm": False,
            "min_volatility_ok": False,
            "max_volatility_ok": False,
            "current_price": 0.0,
            "ema_long_5m": 0.0,
            "ema_medium_5m": 0.0,
            "ema_short_5m": 0.0,
            "rsi_1m": 0.0,
            "prev_rsi_1m": 0.0,
            "macd_1m": 0.0,
            "macd_signal_1m": 0.0,
            "prev_macd_1m": 0.0,
            "prev_macd_signal_1m": 0.0,
            "error": ""
        }

        try:
            # Get 5m klines for downtrend and pullback detection
            prices_5m = binance_data_provider.get_historical_klines("BTCUSDT", "5m", limit=self.ema_long_period + 50).get("prices", [])
            df_5m = normalize_klines(prices_5m, min_length=self.ema_long_period + 5)
            if df_5m.empty:
                detailed_status["data_5m_ok"] = False
                detailed_status["error"] = "Datos 5m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["data_5m_ok"] = True

            df_5m = add_ema(df_5m, self.ema_long_period)
            df_5m = add_ema(df_5m, self.ema_medium_period)
            df_5m = add_ema(df_5m, self.ema_short_period)
            df_5m["ATR"] = ta.volatility.AverageTrueRange(high=df_5m["high"], low=df_5m["low"], close=df_5m["close"], window=self.atr_period).average_true_range() # Añadido
            df_5m[f'volume_ema_{self.volume_ema_period}'] = df_5m['volume'].ewm(span=self.volume_ema_period, adjust=False).mean()


            latest_5m = df_5m.iloc[-1]
            prev_5m = df_5m.iloc[-2]

            detailed_status["current_price"] = latest_5m['close']
            detailed_status["ema_long_5m"] = latest_5m[f'EMA{self.ema_long_period}']
            detailed_status["ema_medium_5m"] = latest_5m[f'EMA{self.ema_medium_period}']
            detailed_status["ema_short_5m"] = latest_5m[f'EMA{self.ema_short_period}']

            # 1. Downtrend Detection
            is_downtrend = (
                latest_5m['close'] <= detailed_status["ema_medium_5m"] * 1.005 and # Close cerca o por debajo de EMA media (Relajado)
                detailed_status["ema_short_5m"] < detailed_status["ema_medium_5m"]
            )
            detailed_status["is_downtrend"] = is_downtrend
            if not is_downtrend:
                return {"signal": "HOLD", "message": f"Tendencia Bajista (Close {latest_5m['close']:.2f} < EMA{self.ema_medium_period} {latest_5m[f'EMA{self.ema_medium_period}']:.2f}): {'✅' if is_downtrend else '❌'}", "detailed_status": detailed_status}

            # 2. Pullback Detection
            is_pullback = (
                latest_5m['close'] >= detailed_status["ema_short_5m"] * 0.99 and # Cierre cerca o por encima de EMA corta (Relajado)
                latest_5m['close'] < detailed_status["ema_medium_5m"] * 1.005 # Cierre cerca o por debajo de EMA media
            )
            detailed_status["is_pullback"] = is_pullback
            if not is_pullback:
                detailed_status["is_pullback"] = False
                detailed_status["error"] = "No hay retroceso detectado dentro de la tendencia bajista."
                return {"signal": "HOLD", "message": f"Pullback (Close {latest_5m['close']:.2f} entre EMA{self.ema_short_period} {detailed_status['ema_short_5m']:.2f} y EMA{self.ema_medium_period} {detailed_status['ema_medium_5m']:.2f}): {'✅' if is_pullback else '❌'}", "detailed_status": detailed_status}

            # Get 1m klines for confirmation indicators
            prices_1m = binance_data_provider.get_historical_klines("BTCUSDT", "1m", limit=self.rsi_period + 50).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=self.rsi_period + 5)
            if df_1m.empty:
                detailed_status["data_1m_ok"] = False
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["data_1m_ok"] = True

            df_1m = add_rsi(df_1m, self.rsi_period)
            macd_1m = ta.trend.MACD(df_1m['close']); df_1m['MACD'] = macd_1m.macd(); df_1m['MACD_Signal'] = macd_1m.macd_signal()

            latest_1m = df_1m.iloc[-1]
            prev_1m = df_1m.iloc[-2]

            detailed_status["rsi_1m"] = latest_1m['RSI']
            detailed_status["prev_rsi_1m"] = prev_1m['RSI']
            detailed_status["macd_1m"] = latest_1m['MACD']
            detailed_status["macd_signal_1m"] = latest_1m['MACD_Signal']
            detailed_status["prev_macd_1m"] = prev_1m['MACD']
            detailed_status["prev_macd_signal_1m"] = prev_1m['MACD_Signal']

            # 3. Confirmation for Pullback End / Downtrend Continuation (Entry Signal)
            # RSI confirmation: RSI was overbought/near overbought and is now turning down
            rsi_confirm = (
                detailed_status["rsi_1m"] < self.rsi_overbought_threshold
            )
            detailed_status["rsi_confirm"] = rsi_confirm

            # MACD confirmation: Bearish crossover on 1m in the last 3 candles
            macd_confirm = any(df_1m['MACD'].iloc[-i] < df_1m['MACD_Signal'].iloc[-i] and df_1m['MACD'].iloc[-i-1] > df_1m['MACD_Signal'].iloc[-i-1] for i in range(1, 4))
            detailed_status["macd_confirm"] = macd_confirm

            # Entry candle confirmation: Current 5m candle closes bearish and below EMA50
            entry_candle_confirm = (
                latest_5m['close'] <= latest_5m['open'] * 1.005 and # Vela bajista o casi bajista (Relajado)
                latest_5m['close'] < detailed_status["ema_medium_5m"] * 1.005 # Cierre cerca o por debajo de EMA media
            )
            detailed_status["entry_candle_confirm"] = entry_candle_confirm

            # Volume Confirmation
            volume_confirm = latest_5m['volume'] > df_5m[f'volume_ema_{self.volume_ema_period}'].iloc[-2] * self.volume_multiplier
            detailed_status["volume_confirm"] = volume_confirm
            
            # Volatility Filter
            min_volatility_ok = latest_5m["ATR"] / latest_5m['close'] > self.min_atr_threshold
            detailed_status["min_volatility_ok"] = min_volatility_ok
            max_volatility_ok = latest_5m["ATR"] / latest_5m['close'] < self.max_atr_threshold
            detailed_status["max_volatility_ok"] = max_volatility_ok

            # Calcular SL y TP basados en ATR
            sl_pct = 0.0
            tp_pct = 0.0
            if not pd.isna(latest_5m["ATR"]):
                sl_pct = (self.sl_multiplier * latest_5m["ATR"] / latest_5m['close']) * 100
                tp_pct = (self.tp_multiplier * latest_5m["ATR"] / latest_5m['close']) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            # Final SELL signal
            main_conditions = [is_downtrend, is_pullback, rsi_confirm, macd_confirm, entry_candle_confirm, volume_confirm, min_volatility_ok, max_volatility_ok]
            if sum(main_conditions) >= 6:
                entry_price = latest_5m['close']

                return {
                    "signal": "SELL",
                    "message": f"Venta detectada: Retroceso en tendencia bajista finalizado con {sum(main_conditions)}/8 confirmaciones.",
                    "entry": entry_price,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }
            
            detailed_status["error"] = "Condiciones de entrada no cumplidas."
            hold_message_parts = []
            hold_message_parts.append(f"Tendencia Bajista (Close {latest_5m['close']:.2f} < EMA{self.ema_medium_period} {detailed_status['ema_medium_5m']:.2f}): {'✅' if detailed_status['is_downtrend'] else '❌'}")
            hold_message_parts.append(f"Pullback (Close {latest_5m['close']:.2f} entre EMA{self.ema_short_period} {detailed_status['ema_short_5m']:.2f} y EMA{self.ema_medium_period} {detailed_status['ema_medium_5m']:.2f}): {'✅' if detailed_status['is_pullback'] else '❌'}")
            hold_message_parts.append(f"RSI Confirmación (RSI {detailed_status['rsi_1m']:.2f} < OB {self.rsi_overbought_threshold:.2f}): {'✅' if detailed_status['rsi_confirm'] else '❌'}")
            hold_message_parts.append(f"MACD Confirmación (Cruce bajista en últimas 3 velas): {'✅' if detailed_status['macd_confirm'] else '❌'}")
            hold_message_parts.append(f"Vela Entrada Bajista (Close {latest_5m['close']:.2f} < Open {latest_5m['open']:.2f} y < EMA{self.ema_medium_period} {detailed_status['ema_medium_5m']:.2f}): {'✅' if detailed_status['entry_candle_confirm'] else '❌'}")
            hold_message_parts.append(f"Confirmación de Volumen (Volumen {latest_5m['volume']:.2f} > EMA Volumen {df_5m[f'volume_ema_{self.volume_ema_period}'].iloc[-2]:.2f} * {self.volume_multiplier}): {'✅' if detailed_status['volume_confirm'] else '❌'}")
            hold_message_parts.append(f"Volatilidad Mínima (ATR/Close {latest_5m['ATR'] / latest_5m['close']:.4f} > {self.min_atr_threshold:.4f}): {'✅' if detailed_status['min_volatility_ok'] else '❌'}")
            hold_message_parts.append(f"Volatilidad Máxima (ATR/Close {latest_5m['ATR'] / latest_5m['close']:.4f} < {self.max_atr_threshold:.4f}): {'✅' if detailed_status['max_volatility_ok'] else '❌'}")

            final_hold_message = " | ".join(hold_message_parts)
            return {"signal": "HOLD", "message": f"Condiciones de entrada no cumplidas: {final_hold_message}", "detailed_status": detailed_status}

        except Exception as e:
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}