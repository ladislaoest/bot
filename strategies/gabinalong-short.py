from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi, scale_aggressiveness
import pandas as pd
import ta.trend
import ta.volatility
import ta.volume
import logging
from strategies.base import BaseStrategy # Añadido

logger = logging.getLogger(__name__)

class GabinalongShort(BaseStrategy): # Heredar de BaseStrategy
    """Estrategia GabinalongShort: Scalping basado en niveles clave de soporte y resistencia con confirmaciones de volumen y velas."""

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level) # Llamada al constructor de la clase base
        # El resto del código de __init__ se mantiene igual
        agg_levels = {
            1: {"tolerance": 0.0010, "volume_multiplier": 1.2},
            2: {"tolerance": 0.0015, "volume_multiplier": 1.1},
            3: {"tolerance": 0.0030, "volume_multiplier": 0.8},
            4: {"tolerance": 0.0035, "volume_multiplier": 0.7},
            5: {"tolerance": 0.0040, "volume_multiplier": 0.6},
            6: {"tolerance": 0.0045, "volume_multiplier": 0.5},
            7: {"tolerance": 0.0050, "volume_multiplier": 0.4},
            8: {"tolerance": 0.0055, "volume_multiplier": 0.3},
            9: {"tolerance": 0.0060, "volume_multiplier": 0.2},
            10: {"tolerance": 0.0065, "volume_multiplier": 0.1}
        }
        level_params = agg_levels.get(aggressiveness_level, agg_levels[5])
        self.volume_multiplier = config.get("volume_multiplier", level_params["volume_multiplier"])
        self.atr_period = config.get("atr_period", 14)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.atr_sma_period = config.get("atr_sma_period", 20)
        self.price_tolerance_factor = config.get("price_tolerance_factor", level_params["tolerance"])
        self.sl_multiplier = config.get("sl_multiplier", 1.5) # Añadido
        self.tp_multiplier = config.get("tp_multiplier", 1.0) # Añadido

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        sl_pct = 0.0 # Inicializar
        tp_pct = 0.0 # Inicializar
        detailed_status = {
            "price_near_support": False,
            "price_near_resistance": False,
            "cond_bullish_candle": False,
            "cond_bearish_candle": False,
            "cond_volume_strong": False,
            "cond_macd_bullish": False,
            "cond_macd_bearish": False,
            "cond_atr_strong": False,
            "error": "",
            "current_price_1m": 0.0,
            "support_level_1d": 0.0,
            "resistance_level_1d": 0.0,
            "tolerance_config": self.price_tolerance_factor,
            "current_volume_1m": 0.0,
            "volume_avg_1m": 0.0,
            "volume_multiplier_config": self.volume_multiplier,
            "current_macd_1m": 0.0,
            "current_macd_signal_1m": 0.0,
            "current_atr_1m": 0.0,
            "atr_sma_1m": 0.0
        }
        try:
            # --- 1. OBTENER DATOS ---
            limit_1m = max(self.atr_period, 20, self.macd_slow, self.atr_sma_period) + 50
            prices_1m = binance_data_provider.get_historical_klines(symbol, "1m", limit=limit_1m).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit_1m - 10)
            if df_1m.empty:
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}

            # --- 2. CALCULAR INDICADORES ---
            df_1m["volume_avg"] = df_1m["volume"].rolling(window=20).mean()
            df_1m["ATR"] = ta.volatility.AverageTrueRange(high=df_1m["high"], low=df_1m["low"], close=df_1m["close"], window=self.atr_period).average_true_range()
            df_1m["ATR_SMA"] = df_1m["ATR"].rolling(window=self.atr_sma_period).mean()
            macd = ta.trend.MACD(df_1m["close"], window_fast=self.macd_fast, window_slow=self.macd_slow, window_sign=self.macd_signal)
            df_1m["MACD"] = macd.macd()
            df_1m["MACD_Signal"] = macd.macd_signal()
            latest_1m = df_1m.iloc[-1]

            daily_klines = binance_data_provider.get_historical_klines(symbol, "1d", limit=2).get("prices", [])
            df_daily = normalize_klines(daily_klines, min_length=1)
            if df_daily.empty:
                detailed_status["error"] = "Datos diarios insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            
            dynamic_resistance_level = df_daily.iloc[-1]['high']
            dynamic_support_level = df_daily.iloc[-1]['low']

            # --- 3. POBLAR DETAILED_STATUS ---
            detailed_status.update({
                "current_price_1m": latest_1m['close'],
                "support_level_1d": dynamic_support_level,
                "resistance_level_1d": dynamic_resistance_level,
                "current_volume_1m": latest_1m['volume'],
                "volume_avg_1m": latest_1m['volume_avg'],
                "current_macd_1m": latest_1m["MACD"],
                "current_macd_signal_1m": latest_1m["MACD_Signal"],
                "current_atr_1m": latest_1m["ATR"],
                "atr_sma_1m": latest_1m["ATR_SMA"]
            })

            # --- 4. VERIFICAR CONDICIONES ---
            cond_bullish_candle = latest_1m['open'] < latest_1m['close']
            cond_bearish_candle = latest_1m['open'] > latest_1m['close']
            cond_volume_strong = latest_1m['volume'] > latest_1m['volume_avg'] * self.volume_multiplier
            cond_macd_bullish = latest_1m["MACD"] > latest_1m["MACD_Signal"]
            cond_macd_bearish = latest_1m["MACD"] < latest_1m["MACD_Signal"]
            cond_atr_strong = latest_1m["ATR"] > latest_1m["ATR_SMA"]
            price_near_support = abs(latest_1m['close'] - dynamic_support_level) <= dynamic_support_level * self.price_tolerance_factor
            price_near_resistance = abs(latest_1m['close'] - dynamic_resistance_level) <= dynamic_resistance_level * self.price_tolerance_factor

            detailed_status.update({
                "price_near_support": price_near_support,
                "price_near_resistance": price_near_resistance,
                "cond_bullish_candle": cond_bullish_candle,
                "cond_bearish_candle": cond_bearish_candle,
                "cond_volume_strong": cond_volume_strong,
                "cond_macd_bullish": cond_macd_bullish,
                "cond_macd_bearish": cond_macd_bearish,
                "cond_atr_strong": cond_atr_strong
            })

            # Calcular SL y TP basados en ATR
            if not df_1m.empty and not pd.isna(latest_1m["ATR"]): # Usar ATR de 1m para SL/TP
                sl_pct = (self.sl_multiplier * latest_1m["ATR"] / latest_1m['close']) * 100
                tp_pct = (self.tp_multiplier * latest_1m["ATR"] / latest_1m['close']) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            # --- 5. LÓGICA DE SEÑAL ---
            buy_conditions = [price_near_support, cond_bullish_candle, cond_volume_strong, cond_macd_bullish, cond_atr_strong]
            if sum(buy_conditions) >= 4:
                entry = latest_1m['close'] # Asumiendo que la entrada es el precio de cierre actual
                return {
                    "signal": "BUY",
                    "message": f"Compra en soporte {dynamic_support_level:.2f} con {sum(buy_conditions)}/5 confirmaciones.",
                    "entry": entry,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            sell_conditions = [price_near_resistance, cond_bearish_candle, cond_volume_strong, cond_macd_bearish, cond_atr_strong]
            if sum(sell_conditions) >= 4:
                entry = latest_1m['close'] # Asumiendo que la entrada es el precio de cierre actual
                return {
                    "signal": "SELL",
                    "message": f"Venta en resistencia {dynamic_resistance_level:.2f} con {sum(sell_conditions)}/5 confirmaciones.",
                    "entry": entry,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # --- 6. MENSAJE HOLD ---
            hold_message_parts = []
            
            current_price_1m = detailed_status["current_price_1m"]
            support_level_1d = detailed_status["support_level_1d"]
            resistance_level_1d = detailed_status["resistance_level_1d"]
            tolerance_config = detailed_status["tolerance_config"]
            
            volume_current = detailed_status["current_volume_1m"]
            volume_avg = detailed_status["volume_avg_1m"]
            volume_multiplier_config = detailed_status["volume_multiplier_config"]
            
            macd_current = detailed_status["current_macd_1m"]
            macd_signal_current = detailed_status["current_macd_signal_1m"]
            
            atr_current = detailed_status["current_atr_1m"]
            atr_sma_current = detailed_status["atr_sma_1m"]

            if price_near_support or price_near_resistance:
                if price_near_support:
                    hold_message_parts.append(f"Cerca de Soporte (Actual: {current_price_1m:.2f}, Soporte: {support_level_1d:.2f}, Tolerancia: {support_level_1d * tolerance_config:.2f}): {'✅' if price_near_support else '❌'}")
                    hold_message_parts.append(f"Vela Alcista (Open: {latest_1m['open']:.2f}, Close: {latest_1m['close']:.2f}): {'✅' if cond_bullish_candle else '❌'}")
                    hold_message_parts.append(f"Volumen Fuerte (Actual: {volume_current:.2f}, Esperado > {volume_avg * volume_multiplier_config:.2f}): {'✅' if cond_volume_strong else '❌'}")
                    hold_message_parts.append(f"MACD Alcista (MACD: {macd_current:.2f}, Signal: {macd_signal_current:.2f}): {'✅' if cond_macd_bullish else '❌'}")
                    hold_message_parts.append(f"ATR Fuerte (ATR: {atr_current:.2f}, SMA: {atr_sma_current:.2f}): {'✅' if cond_atr_strong else '❌'}")
                
                if price_near_resistance:
                    hold_message_parts.append(f"Cerca de Resistencia (Actual: {current_price_1m:.2f}, Resistencia: {resistance_level_1d:.2f}, Tolerancia: {resistance_level_1d * tolerance_config:.2f}): {'✅' if price_near_resistance else '❌'}")
                    hold_message_parts.append(f"Vela Bajista (Open: {latest_1m['open']:.2f}, Close: {latest_1m['close']:.2f}): {'✅' if cond_bearish_candle else '❌'}")
                    hold_message_parts.append(f"Volumen Fuerte (Actual: {volume_current:.2f}, Esperado > {volume_avg * volume_multiplier_config:.2f}): {'✅' if cond_volume_strong else '❌'}")
                    hold_message_parts.append(f"MACD Bajista (MACD: {macd_current:.2f}, Signal: {macd_signal_current:.2f}): {'✅' if cond_macd_bearish else '❌'}")
                    hold_message_parts.append(f"ATR Fuerte (ATR: {atr_current:.2f}, SMA: {atr_sma_current:.2f}): {'✅' if cond_atr_strong else '❌'}")
            else:
                hold_message_parts.append(f"Esperando que el precio se acerque a Soporte ({support_level_1d:.2f}) o Resistencia ({resistance_level_1d:.2f}). Precio actual: {current_price_1m:.2f}")

            final_hold_message = " | ".join(hold_message_parts)
            return {"signal": "HOLD", "message": final_hold_message, "detailed_status": detailed_status}

        except Exception as e:
            logger.error(f"GabinalongShort error: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
