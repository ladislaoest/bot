from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi
import pandas as pd
import ta.volatility
import ta.momentum
import ta.volume
import logging
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

class LateralReversal(BaseStrategy):
    """
    Estrategia de Reversión Lateral: Busca oportunidades de compra/venta en retrocesos
    dentro de un rango lateral, utilizando Bandas de Bollinger, RSI y volumen.
    """

    def __init__(self, config=None, aggressiveness_level=3):
        super().__init__(config, aggressiveness_level)

        # Parámetros de Bandas de Bollinger
        self.bb_window = config.get('bb_window', 20)
        self.bb_window_dev = config.get('bb_window_dev', 2.0)

        # Parámetros de RSI
        self.rsi_window = config.get('rsi_window', 14)
        
        # Niveles de agresividad para RSI
        agg_levels_rsi_ob = {
            1: 80, 2: 75, 3: 70, 4: 65, 5: 60,
            6: 55, 7: 50, 8: 45, 9: 40, 10: 35
        }
        agg_levels_rsi_os = {
            1: 20, 2: 25, 3: 30, 4: 35, 5: 40,
            6: 45, 7: 50, 8: 55, 9: 60, 10: 65
        }
        
        self.rsi_overbought = config.get('rsi_overbought', agg_levels_rsi_ob.get(aggressiveness_level, 70))
        self.rsi_oversold = config.get('rsi_oversold', agg_levels_rsi_os.get(aggressiveness_level, 30))

        # Parámetros de ATR
        self.atr_window = config.get('atr_window', 14)
        self.sl_atr_multiplier = config.get('sl_atr_multiplier', 1.5)
        self.tp_atr_multiplier = config.get('tp_atr_multiplier', 1.0)

        # Parámetros de Volumen
        self.volume_window = config.get('volume_window', 20)
        self.volume_multiplier = config.get('volume_multiplier', 1.0) # Reducido para ser más permisivo

        # Factor de tolerancia para Bandas de Bollinger (nuevo parámetro)
        agg_levels_bb_tolerance = {
            1: 0.0005, 5: 0.0010, 10: 0.0015
        }
        self.bb_tolerance_factor = config.get('bb_tolerance_factor', agg_levels_bb_tolerance.get(aggressiveness_level, 0.0010))

    def run(self, capital_client_api, binance_data_provider, symbol="BTCUSDT"):
        detailed_status = {
            "data_ok": False,
            "bb_lower_band": 0.0,
            "bb_upper_band": 0.0,
            "current_price": 0.0,
            "rsi_value": 0.0,
            "rsi_oversold_ok": False,
            "rsi_overbought_ok": False,
            "bullish_candle_confirm": False,
            "bearish_candle_confirm": False,
            "volume_confirm": False,
            "atr_value": 0.0,
            "error": "",
            "sl_pct": 0.0,
            "tp_pct": 0.0
        }
        sl_pct = 0.0 # Inicializar
        tp_pct = 0.0 # Inicializar

        try:
            # --- 1. OBTENER DATOS ---
            # Necesitamos suficientes datos para BB, RSI, ATR y Volumen
            limit = max(self.bb_window, self.rsi_window, self.atr_window, self.volume_window) + 50
            prices_1m = binance_data_provider.get_historical_klines(symbol, "1m", limit=limit).get("prices", [])
            df_1m = normalize_klines(prices_1m, min_length=limit - 10)

            if df_1m.empty:
                detailed_status["error"] = "Datos 1m insuficientes."
                return {"signal": "HOLD", "message": detailed_status["error"], "detailed_status": detailed_status}
            detailed_status["data_ok"] = True

            # --- 2. CALCULAR INDICADORES ---
            # Bandas de Bollinger
            df_1m["BBL"] = ta.volatility.bollinger_lband(df_1m["close"], window=self.bb_window, window_dev=self.bb_window_dev)
            df_1m["BBU"] = ta.volatility.bollinger_hband(df_1m["close"], window=self.bb_window, window_dev=self.bb_window_dev)

            # RSI
            df_1m["RSI"] = ta.momentum.rsi(df_1m["close"], window=self.rsi_window)

            # ATR
            df_1m["ATR"] = ta.volatility.average_true_range(df_1m["high"], df_1m["low"], df_1m["close"], window=self.atr_window)

            # Volumen promedio
            df_1m["Volume_MA"] = df_1m["volume"].rolling(window=self.volume_window).mean()

            latest_candle = df_1m.iloc[-1]
            prev_candle = df_1m.iloc[-2]

            detailed_status.update({
                "current_price": latest_candle["close"],
                "bb_lower_band": latest_candle["BBL"],
                "bb_upper_band": latest_candle["BBU"],
                "rsi_value": latest_candle["RSI"],
                "atr_value": latest_candle["ATR"]
            })

            # --- 3. VERIFICAR CONDICIONES ---
            current_price = latest_candle["close"]

            # Condiciones de RSI
            rsi_oversold_ok = latest_candle["RSI"] < self.rsi_oversold
            rsi_overbought_ok = latest_candle["RSI"] > self.rsi_overbought
            detailed_status["rsi_oversold_ok"] = rsi_oversold_ok
            detailed_status["rsi_overbought_ok"] = rsi_overbought_ok

            # Confirmación de vela
            bullish_candle_confirm = latest_candle["close"] > latest_candle["open"] # Vela alcista
            bearish_candle_confirm = latest_candle["close"] < latest_candle["open"] # Vela bajista
            detailed_status["bullish_candle_confirm"] = bullish_candle_confirm
            detailed_status["bearish_candle_confirm"] = bearish_candle_confirm

            # Confirmación de volumen
            volume_confirm = latest_candle["volume"] > latest_candle["Volume_MA"] * self.volume_multiplier
            detailed_status["volume_confirm"] = volume_confirm

            # Calcular SL y TP
            sl_pct = (self.sl_atr_multiplier * latest_candle["ATR"] / current_price) * 100
            tp_pct = (self.tp_atr_multiplier * latest_candle["ATR"] / current_price) * 100
            detailed_status["sl_pct"] = sl_pct
            detailed_status["tp_pct"] = tp_pct

            # --- 4. LÓGICA DE SEÑAL ---

            # Señal de COMPRA (Reversión alcista desde el soporte)
            buy_conditions = [
                current_price <= latest_candle["BBL"] * (1 + self.bb_tolerance_factor), # Precio toca o cruza la banda inferior (con tolerancia)
                rsi_oversold_ok, # RSI en sobreventa
                bullish_candle_confirm, # Vela alcista de confirmación
                volume_confirm # Volumen por encima del promedio
            ]
            if sum(buy_conditions) >= 4:
                entry_price = current_price
                return {
                    "signal": "BUY",
                    "message": f"Compra: Reversión alcista desde BB inferior con {sum(buy_conditions)}/4 confirmaciones. RSI: {latest_candle['RSI']:.2f}",
                    "entry": entry_price,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # Señal de VENTA (Reversión bajista desde la resistencia)
            sell_conditions = [
                current_price >= latest_candle["BBU"] * (1 - self.bb_tolerance_factor), # Precio toca o cruza la banda superior (con tolerancia)
                rsi_overbought_ok, # RSI en sobrecompra
                bearish_candle_confirm, # Vela bajista de confirmación
                volume_confirm # Volumen por encima del promedio
            ]
            if sum(sell_conditions) >= 4:
                entry_price = current_price
                return {
                    "signal": "SELL",
                    "message": f"Venta: Reversión bajista desde BB superior con {sum(sell_conditions)}/4 confirmaciones. RSI: {latest_candle['RSI']:.2f}",
                    "entry": entry_price,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "detailed_status": detailed_status
                }

            # --- 5. MENSAJE HOLD ---
            hold_message_parts = []
            hold_message_parts.append(f"Precio: {current_price:.2f} (BB Inferior: {latest_candle['BBL']:.2f}, BB Superior: {latest_candle['BBU']:.2f})")
            hold_message_parts.append(f"RSI: {latest_candle['RSI']:.2f} (OS: {self.rsi_oversold}, OB: {self.rsi_overbought})")
            hold_message_parts.append(f"Vela Alcista: {'✅' if bullish_candle_confirm else '❌'}")
            hold_message_parts.append(f"Vela Bajista: {'✅' if bearish_candle_confirm else '❌'}")
            
            # Añadir detalles de volumen
            volume_current = latest_candle["volume"]
            volume_expected_threshold = latest_candle["Volume_MA"] * self.volume_multiplier
            hold_message_parts.append(f"Volumen: {volume_current:.2f} (Esperado > {volume_expected_threshold:.2f}) {'✅' if volume_confirm else '❌'}")

            final_hold_message = " | ".join(hold_message_parts)

            return {
                "signal": "HOLD",
                "message": f"Esperando condiciones de reversión lateral: {final_hold_message}",
                "detailed_status": detailed_status
            }

        except Exception as e:
            logger.error(f"Error en LateralReversal: {str(e)}")
            detailed_status["error"] = str(e)
            return {"signal": "ERROR", "message": str(e), "detailed_status": detailed_status}
