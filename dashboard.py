import streamlit as st
import pandas as pd
import os
import json
import time
import requests
from dotenv import load_dotenv
from capital_bot import CapitalComAPIClient, BinanceAPIClient, TradingBot, TelegramListener, load_strategy_classes, load_config, get_default_strategy_params, config_lock
import threading
from datetime import datetime, timedelta # Importar datetime y timedelta
import logging
import logging.handlers

# Configuración del logger
LOG_FILE = "bot_logs.log"
logger = logging.getLogger("DashboardLogger")
logger.setLevel(logging.DEBUG)

# Handler para escribir en archivo


# Handler para consola
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

# --- Funciones de Utilidad para Logs ---
def read_bot_logs(log_file="bot_logs.log", num_lines=100):
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return lines[-num_lines:]
    except Exception as e:
        st.error(f"Error al leer el archivo de logs: {e}")
        return [f"Error al leer logs: {e}"]

# --- Funciones de Utilidad ---
def load_trade_history(file_path="trade_history.csv"):
    if not os.path.exists(file_path):
        st.warning(f"Archivo de historial de operaciones no encontrado: {file_path}")
        return pd.DataFrame()
    try:
        df = pd.read_csv(file_path, on_bad_lines='skip')
        original_rows = len(df)
        
        # Count open trades before any modification
        open_trades_count = df[df['status'] == 'OPEN'].shape[0]

        df['profit_loss'] = pd.to_numeric(df['profit_loss'], errors='coerce')
        df['profit_loss'] = df['profit_loss'].fillna(0)
        
        removed_rows = original_rows - len(df)

        if removed_rows > 0:
            # Check if the number of removed rows is equal to the number of open trades
            if removed_rows == open_trades_count and open_trades_count > 0:
                st.info(f"Se ignoraron {open_trades_count} operaciones abiertas para el análisis (aún no tienen P/L).")
            else:
                st.warning(f"Se eliminaron {removed_rows} filas del historial debido a valores inválidos en 'profit_loss'.")

        # Asegurar que 'stop_loss' y 'take_profit' existan y sean numéricos
        for col in ['stop_loss', 'take_profit']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0.0 # Añadir la columna si no existe con valores por defecto

        df = df.sort_values(by="open_time", ascending=False)
        return df
    except Exception as e:
        st.error(f"Error al cargar o procesar el historial de operaciones: {e}")
        return pd.DataFrame()

def initialize_bot():
    logger.debug("initialize_bot() called.") # New line
    if st.session_state.get('bot_initialized'):
        print("DEBUG: Bot ya inicializado.")
        return True
    try:
        print("DEBUG: Iniciando initialize_bot()")
        load_dotenv()
        print(f"DEBUG: BINANCE_API_KEY cargada: {bool(os.getenv('BINANCE_API_KEY'))}")
        print(f"DEBUG: BINANCE_API_SECRET cargada: {bool(os.getenv('BINANCE_API_SECRET'))}")
        st.session_state.config = load_config()
        print("DEBUG: Configuración cargada.")
        
        # --- Lógica de inicialización de CapitalComAPIClient y TradingBot ---
        # Esta lógica se ha movido aquí desde el bloque if __name__ == "__main__": en capital_bot.py
        # para asegurar que Streamlit maneje la inicialización de la instancia principal.
        
        # Crear una instancia temporal de CapitalComAPIClient para obtener las cuentas
        temp_capital_client = CapitalComAPIClient()
        accounts = temp_capital_client.get_accounts()

        # Determinar el account_id deseado
        target_account_name = "bot"
        selected_account_id = None
        normalized_target_account_name = target_account_name.strip().lower()
        for account in accounts.get('accounts', []):
            normalized_account_name = account.get('accountName', '').strip().lower()
            if normalized_account_name == normalized_target_account_name:
                selected_account_id = account.get('accountId')
                break
        
        if selected_account_id is None:
            st.error(f"Error: No se pudo encontrar la cuenta '{target_account_name}'. Cuentas disponibles: {[acc.get('accountName') for acc in accounts.get('accounts', [])]}."
)
            return False # Detener la inicialización si no se encuentra la cuenta
        
        # Crear la instancia final de CapitalComAPIClient con el account_id correcto
        capital_client = CapitalComAPIClient(account_id=selected_account_id)
        
        st.session_state.capital_client_api = capital_client # Almacenar la instancia en session_state
        print("DEBUG: CapitalComAPIClient inicializado con account_id correcto.")

        st.session_state.binance_client_api = BinanceAPIClient(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
        print("DEBUG: BinanceAPIClient inicializado.")
        
        # Crear la instancia de TradingBot con la instancia correcta de CapitalComAPIClient
        st.session_state.bot = TradingBot(st.session_state.capital_client_api, st.session_state.binance_client_api)
        print("DEBUG: TradingBot inicializado.")
        
        if not st.session_state.get('listener_started'):
            st.session_state.listener = TelegramListener(st.session_state.bot)
            print("DEBUG: TelegramListener iniciado.")
            st.session_state.listener.start()
            print("DEBUG: TelegramListener iniciado.")
            st.session_state.listener_started = True

        st.session_state.bot_initialized = True
        return True
    except Exception as e:
        st.error(f"Error al inicializar el bot: {e}")
        print(f"ERROR: Excepción en initialize_bot(): {e}")
        return False

def format_detailed_status_string(strategy_name, strategy_instance, detailed_status):
    if not detailed_status:
        return "No hay detalles disponibles."

    parts = []
    
    # Common fields
    if "current_price" in detailed_status:
        parts.append(f"Precio actual: {detailed_status['current_price']:.2f}")
    if "atr_val" in detailed_status:
        parts.append(f"ATR: {detailed_status['atr_val']:.2f}")

    # Strategy-specific fields (using strategy_instance for periods/thresholds)
    if hasattr(strategy_instance, 'ema_fast_period') and "ema_fast_val" in detailed_status:
        parts.append(f"EMA Rápida ({strategy_instance.ema_fast_period}): {detailed_status['ema_fast_val']:.2f}")
    if hasattr(strategy_instance, 'ema_slow_period') and "ema_slow_val" in detailed_status:
        parts.append(f"EMA Lenta ({strategy_instance.ema_slow_period}): {detailed_status['ema_slow_val']:.2f}")
    if hasattr(strategy_instance, 'ema_long_trend_period') and "ema_long_trend_ok" in detailed_status: # Check if this key exists
        parts.append(f"EMA Larga Tendencia ({strategy_instance.ema_long_trend_period}): {detailed_status['ema_long_trend_ok']}") # This should be ema_long_trend_val, not ok

    if hasattr(strategy_instance, 'rsi_period') and "rsi_val" in detailed_status:
        rsi_threshold = ""
        if hasattr(strategy_instance, 'rsi_buy_threshold'):
            rsi_threshold = f" (Umbral: {strategy_instance.rsi_buy_threshold})"
        elif hasattr(strategy_instance, 'rsi_sell_threshold'):
            rsi_threshold = f" (Umbral: {strategy_instance.rsi_sell_threshold})"
        parts.append(f"RSI ({strategy_instance.rsi_period}): {detailed_status['rsi_val']:.2f}{rsi_threshold}")

    if "volume_val" in detailed_status:
        parts.append(f"Volumen actual: {detailed_status['volume_val']:.2f}")
    if "volume_avg_val" in detailed_status:
        parts.append(f"Volumen promedio: {detailed_status['volume_avg_val']:.2f}")
    
    if "macd_val" in detailed_status:
        parts.append(f"MACD: {detailed_status['macd_val']:.2f}")
    if "macd_signal_val" in detailed_status:
        parts.append(f"MACD Signal: {detailed_status['macd_signal_val']:.2f}")

    # Boolean flags
    if "trend_ok" in detailed_status:
        parts.append(f"Tendencia OK: {'Sí' if detailed_status['trend_ok'] else 'No'}")
    if "long_trend_ok" in detailed_status:
        parts.append(f"Tendencia Larga OK: {'Sí' if detailed_status['long_trend_ok'] else 'No'}")
    if "volatility_ok" in detailed_status:
        parts.append(f"Volatilidad OK: {'Sí' if detailed_status['volatility_ok'] else 'No'}")
    if "rsi_ok" in detailed_status:
        parts.append(f"RSI OK: {'Sí' if detailed_status['rsi_ok'] else 'No'}")
    if "volume_ok" in detailed_status:
        parts.append(f"Volumen OK: {'Sí' if detailed_status['volume_ok'] else 'No'}")
    if "macd_bullish_ok" in detailed_status:
        parts.append(f"MACD Alcista OK: {'Sí' if detailed_status['macd_bullish_ok'] else 'No'}")
    if "macd_bearish_ok" in detailed_status:
        parts.append(f"MACD Bajista OK: {'Sí' if detailed_status['macd_bearish_ok'] else 'No'}")
    if "resistance_level" in detailed_status:
        parts.append(f"Nivel de Resistencia: {detailed_status['resistance_level']:.2f}")
    if "support_level" in detailed_status:
        parts.append(f"Nivel de Soporte: {detailed_status['support_level']:.2f}")
    if "breakout_ok" in detailed_status:
        parts.append(f"Ruptura OK: {'Sí' if detailed_status['breakout_ok'] else 'No'}")
    if "retest_ok" in detailed_status:
        parts.append(f"Retesteo OK: {'Sí' if detailed_status['retest_ok'] else 'No'}")
    if "pullback_rebound_ok" in detailed_status:
        parts.append(f"Pullback OK: {'Sí' if detailed_status['pullback_rebound_ok'] else 'No'}")
    if "rsi_healthy_ok" in detailed_status:
        parts.append(f"RSI Saludable OK: {'Sí' if detailed_status['rsi_healthy_ok'] else 'No'}")
    if "macd_ok" in detailed_status: # For Ladis, which has macd_ok
        parts.append(f"MACD OK: {'Sí' if detailed_status['macd_ok'] else 'No'}")


    # Error message
    if "error" in detailed_status and detailed_status["error"]:
        parts.append(f"Error: {detailed_status['error']}")

    return " | ".join(parts)

# --- Componentes de la UI ---
def sync_ui():
    with st.expander("Sincronización de Historial"):
        st.info("Sube tu historial local para combinarlo con el de Render, o descarga el historial de Render a tu PC.")
        
        # --- Lógica de Subida ---
        uploaded_file = st.file_uploader(
            "Sube tu 'trade_history.csv' local",
            type=['csv']
        )
        
        if uploaded_file is not None:
            try:
                # NOTA: En un entorno de producción en Render, esta ruta debería apuntar a un disco persistente.
                history_file_path = "trade_history.csv" 
                
                local_df = pd.read_csv(uploaded_file)
                
                if os.path.exists(history_file_path):
                    remote_df = pd.read_csv(history_file_path)
                    combined_df = pd.concat([remote_df, local_df])
                else:
                    combined_df = local_df

                if 'dealReference' in combined_df.columns:
                    combined_df.drop_duplicates(subset=['dealReference'], keep='last', inplace=True)
                
                combined_df.sort_values(by="open_time", ascending=False, inplace=True)
                
                combined_df.to_csv(history_file_path, index=False)
                
                st.success("¡Historial combinado y guardado con éxito!")
                time.sleep(2)
                st.rerun()

            except Exception as e:
                st.error(f"Ocurrió un error al procesar el archivo: {e}")

        # --- Lógica de Descarga ---
        history_file_path = "trade_history.csv"
        if os.path.exists(history_file_path):
            with open(history_file_path, "rb") as f:
                st.download_button(
                    label="Descargar Historial de Render",
                    data=f,
                    file_name="trade_history_render.csv",
                    mime="text/csv"
                )

def bot_controls_ui():
    with st.expander("Controles del Bot", expanded=False):
        st.subheader("Gestión del Historial de Operaciones")
        if st.button("Limpiar Historial de Operaciones"):
            if st.session_state.get('bot'):
                message = st.session_state.bot.clear_trade_history()
                st.success(message)
                time.sleep(1)
                st.rerun()
            else:
                st.warning("El bot no está inicializado. Conéctate primero.")

        st.subheader("Nivel de Agresividad")
        st.info("Define el perfil de riesgo del bot. Un nivel más bajo es más conservador (menos operaciones, señales más fuertes), mientras que un nivel más alto es más agresivo (más operaciones, mayor riesgo).")
        current_level = st.session_state.config.get("global_settings", {}).get("aggressiveness_level", 3)
        
        level = st.slider("Selecciona el nivel (1=Conservador, 10=Agresivo)", 1, 10, current_level)

        if level != current_level:
            st.session_state.config["global_settings"]["aggressiveness_level"] = level
            with config_lock:
                with open("config.json", "w") as f:
                    json.dump(st.session_state.config, f, indent=2)
            
            if st.session_state.get('bot'):
                st.session_state.bot.aggressiveness_level = level
                st.session_state.bot.reload_all_strategy_configs()
                st.success(f"Nivel de agresividad cambiado a {level}. Estrategias recargadas.")
            else:
                st.success(f"Nivel de agresividad cambiado a {level}. Se aplicará al conectar el bot.")
            
            time.sleep(1)
            st.rerun()

        st.subheader("Filtro Maestro Contra-Tendencia")
        st.info("Si está activada, el bot bloqueará todas las operaciones de COMPRA si la tendencia principal (30m) es bajista, y todas las de VENTA si es alcista.")
        current_prevent_counter_trend = st.session_state.config.get("global_settings", {}).get("prevent_counter_trend_trades", True)
        
        enable_prevent_counter_trend = st.checkbox("Activar Filtro Maestro Contra-Tendencia", value=current_prevent_counter_trend)

        if enable_prevent_counter_trend != current_prevent_counter_trend:
            st.session_state.config["global_settings"]["prevent_counter_trend_trades"] = enable_prevent_counter_trend
            with config_lock:
                with open("config.json", "w") as f:
                    json.dump(st.session_state.config, f, indent=2)
            
            if st.session_state.get('bot'):
                st.session_state.bot.prevent_counter_trend_trades = enable_prevent_counter_trend
                st.success(f"Filtro Maestro Contra-Tendencia {'activado' if enable_prevent_counter_trend else 'desactivado'}.")
            else:
                st.success(f"Filtro Maestro Contra-Tendencia {'activado' if enable_prevent_counter_trend else 'desactivado'}. Se aplicará al conectar el bot.")
            
            time.sleep(1)
            st.rerun()

        st.subheader("Regla SL=TP contra Tendencia")
        st.info("Si está activada, el Take Profit (TP) se ajustará para ser igual al Stop Loss (SL) cuando una operación se abra en contra de la tendencia principal de 30 minutos.")
        current_tp_sl_against_trend = st.session_state.config.get("global_settings", {}).get("enable_tp_sl_against_trend", False)
        
        enable_tp_sl = st.checkbox("Activar SL=TP contra Tendencia", value=current_tp_sl_against_trend)

        if enable_tp_sl != current_tp_sl_against_trend:
            st.session_state.config["global_settings"]["enable_tp_sl_against_trend"] = enable_tp_sl
            with config_lock:
                with open("config.json", "w") as f:
                    json.dump(st.session_state.config, f, indent=2)
            
            if st.session_state.get('bot'):
                st.session_state.bot.enable_tp_sl_against_trend = enable_tp_sl
                st.success(f"Regla SL=TP contra Tendencia {'activada' if enable_tp_sl else 'desactivada'}.")
            else:
                st.success(f"Regla SL=TP contra Tendencia {'activada' if enable_tp_sl else 'desactivada'}. Se aplicará al conectar el bot.")
            
            time.sleep(1)
            st.rerun()

        st.subheader("Abrir Dos Operaciones con TP Diferente")
        st.info("Si está activada, el bot abrirá dos operaciones por señal (excepto si se aplica la regla SL=TP contra tendencia): una con el TP estándar y otra con el TP reducido en 0.10%.")
        current_two_tp_trades = st.session_state.config.get("global_settings", {}).get("enable_two_tp_trades", False)
        
        enable_two_tp = st.checkbox("Activar Dos Operaciones con TP Diferente", value=current_two_tp_trades)

        if enable_two_tp != current_two_tp_trades:
            st.session_state.config["global_settings"]["enable_two_tp_trades"] = enable_two_tp
            with config_lock:
                with open("config.json", "w") as f:
                    json.dump(st.session_state.config, f, indent=2)
            
            if st.session_state.get('bot'):
                st.session_state.bot.enable_two_tp_trades = enable_two_tp
                st.success(f"Opción 'Dos Operaciones con TP Diferente' {'activada' if enable_two_tp else 'desactivada'}.")
            else:
                st.success(f"Opción 'Dos Operaciones con TP Diferente' {'activada' if enable_two_tp else 'desactivada'}. Se aplicará al conectar el bot.")
            
            time.sleep(1)
            st.rerun()

        st.subheader("Tamaño de Lote Global")
        st.info("Define el tamaño de cada operación en el bot (ej. 0.0015 para 0.0015 BTC).")
        current_order_size = st.session_state.config.get("global_settings", {}).get("global_order_size", 0.0015)
        
        new_order_size = st.number_input("Tamaño de Lote", value=current_order_size, format="%.4f", step=0.0001)

        if new_order_size != current_order_size:
            if st.session_state.get('bot'):
                message = st.session_state.bot.set_global_order_size(new_order_size)
                st.success(message)
            else:
                st.session_state.config["global_settings"]["global_order_size"] = new_order_size
                with config_lock:
                    with open("config.json", "w") as f:
                        json.dump(st.session_state.config, f, indent=2)
                st.success(f"Tamaño de lote global cambiado a {new_order_size}. Se aplicará al conectar el bot.")
            
            time.sleep(1)
            st.rerun()

def manage_strategies_ui():
    with st.expander("Gestión de Estrategias", expanded=False):
        st.info("Selecciona las estrategias que se activarán al iniciar el bot.")
        config_data = load_config()
        strategy_classes = load_strategy_classes()
        if not strategy_classes:
            st.warning("No se encontraron estrategias.")
            return

        for strategy_name in sorted(strategy_classes.keys()):
            is_active = config_data.get(strategy_name, {}).get('is_active', True)
            if st.checkbox(strategy_name, value=is_active, key=f"chk_{strategy_name}"):
                if not is_active:
                    config_data[strategy_name]['is_active'] = True
                    with config_lock:
                        with open("config.json", "w") as f: json.dump(config_data, f, indent=2)
                    if st.session_state.get('bot'): st.session_state.bot.set_strategy(strategy_name, enable=True)
                    st.rerun()
            elif is_active:
                config_data[strategy_name]['is_active'] = False
                with config_lock:
                    with open("config.json", "w") as f: json.dump(config_data, f, indent=2)
                if st.session_state.get('bot'): st.session_state.bot.set_strategy(strategy_name, enable=False)
                st.rerun()

def strategy_parameters_ui():
    with st.expander("Parámetros de Estrategias", expanded=False):
        st.info("Ajusta y guarda los parámetros para cada estrategia.")
        config_data = load_config()
        strategy_classes = load_strategy_classes()
        
        if not strategy_classes:
            st.warning("No se encontraron estrategias.")
            return

        strategy_names = sorted(strategy_classes.keys())
        selected_strategy_name = st.selectbox("Selecciona una estrategia", strategy_names, key="strategy_selector")

        if selected_strategy_name:
            s_class = strategy_classes[selected_strategy_name]
            name = selected_strategy_name

            with st.container():
                st.subheader(name)
                default_params = get_default_strategy_params(s_class)
                current_params = config_data.get(name, {})
                # Ensure all default params are in current_params for the UI
                for p_name, p_val in default_params.items():
                    if p_name not in current_params:
                        current_params[p_name] = p_val
                
                updated_params = current_params.copy()
                for param, value in current_params.items():
                    if param == 'is_active': continue
                    if isinstance(value, bool): updated_params[param] = st.checkbox(param, value, key=f"{name}_{param}")
                    elif isinstance(value, int):
                        updated_params[param] = st.number_input(param, value=value, key=f"{name}_{param}")
                    elif isinstance(value, float):
                        updated_params[param] = st.number_input(param, value=value, key=f"{name}_{param}", format="%.5f")
                    else: updated_params[param] = st.text_input(param, value, key=f"{name}_{param}")
                
                if st.button("Guardar", key=f"save_{name}"):
                    config_data[name] = updated_params
                    with config_lock:
                        with open("config.json", "w") as f: json.dump(config_data, f, indent=2)
                    if st.session_state.get('bot'): st.session_state.bot.reload_all_strategy_configs()
                    st.success(f"Parámetros de {name} guardados.")
                    print(f"DEBUG: Parámetros guardados para {name}: {updated_params}") # DEBUG PRINT
                    time.sleep(1)

@st.dialog("Rendimiento por Estrategia")
def show_performance_by_strategy(closed_trades):
    if not closed_trades.empty:
        st.subheader("Rendimiento por Estrategia")
        perf_by_strat = closed_trades.groupby('strategy')['profit_loss'].agg(['sum', 'count', lambda x: (x>0).sum()])
        perf_by_strat.columns = ['P/L Total', 'Trades', 'Ganadoras']
        perf_by_strat['Tasa de Acierto'] = (perf_by_strat['Ganadoras'] / perf_by_strat['Trades'] * 100)
        st.dataframe(perf_by_strat.style.format({'P/L Total': '{:+.2f}', 'Tasa de Acierto': '{:.2f}%'}), width='stretch')
    else:
        st.info("No hay operaciones cerradas para calcular el rendimiento por estrategia.")






@st.dialog("Análisis Detallado de Estrategias")
def show_detailed_strategy_analysis(closed_trades):
    if not closed_trades.empty:
        st.subheader("Análisis Detallado de Estrategias")
        # Convertir a datetime para calcular la duración
        closed_trades['open_time'] = pd.to_datetime(closed_trades['open_time'])
        closed_trades['close_time'] = pd.to_datetime(closed_trades['close_time'])

        for strategy_name in closed_trades['strategy'].unique():
            st.markdown(f"### {strategy_name}")
            strat_trades = closed_trades[closed_trades['strategy'] == strategy_name].sort_values(by="open_time", ascending=False)

            total_trades = strat_trades.shape[0]
            winning_trades = strat_trades[strat_trades['profit_loss'] > 0].shape[0]
            losing_trades = total_trades - winning_trades
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

            total_pnl = strat_trades['profit_loss'].sum()
            avg_pnl_per_trade = total_pnl / total_trades if total_trades > 0 else 0

            # Duración promedio de las operaciones
            strat_trades.loc[:, 'duration'] = (strat_trades['close_time'] - strat_trades['open_time']).dt.total_seconds() / 60 # en minutos
            avg_duration = strat_trades['duration'].mean() if total_trades > 0 else 0

            # Calcular Risk/Reward Ratio promedio
            valid_ratios = strat_trades[(strat_trades['stop_loss'].notna()) & (strat_trades['take_profit'].notna()) & (strat_trades['stop_loss'] != 0) & (strat_trades['take_profit'] != 0)]
            
            risk_reward_ratios = []
            for index, trade in valid_ratios.iterrows():
                if trade['signal'] == 'BUY':
                    risk = trade['entry_price'] - trade['stop_loss']
                    reward = trade['take_profit'] - trade['entry_price']
                elif trade['signal'] == 'SELL':
                    risk = trade['stop_loss'] - trade['entry_price']
                    reward = trade['entry_price'] - trade['take_profit']
                else:
                    risk = 0
                    reward = 0

                if risk > 0:
                    risk_reward_ratios.append(reward / risk)
            
            avg_risk_reward = pd.Series(risk_reward_ratios).mean() if risk_reward_ratios else 0

            # --- Resumen General ---
            st.write(f"**Total de Operaciones:** {total_trades}")
            st.write(f"**Operaciones Ganadoras:** {winning_trades}")
            st.write(f"**Operaciones Perdedoras:** {losing_trades}")
            st.write(f"**Tasa de Acierto:** {win_rate:.2f}%")
            st.write(f"**P/L Total:** {total_pnl:+.2f}")
            st.write(f"**P/L Promedio por Operación:** {avg_pnl_per_trade:+.2f}")
            st.write(f"**Duración Promedio de Operación:** {avg_duration:.2f} minutos")
            st.write(f"**Ratio Riesgo/Recompensa Promedio:** {avg_risk_reward:.2f}")

            # --- Consejos ---
            with st.expander("Análisis y Consejos", expanded=False):
                if total_trades == 0:
                    st.info(f"No hay operaciones cerradas para {strategy_name}.")
                else:
                    if win_rate >= 60 and avg_pnl_per_trade > 0:
                        st.success(f"La estrategia {strategy_name} es muy efectiva con una alta tasa de acierto. ¡Excelente rendimiento!")
                        st.info(f"**Consejo:** Considera aumentar ligeramente el tamaño de la posición o buscar oportunidades para escalar las ganancias si el ratio R/R lo permite.")
                    elif win_rate >= 40 and avg_pnl_per_trade > 0:
                        st.info(f"La estrategia {strategy_name} es rentable. Su tasa de acierto es buena, pero podría mejorarse.")
                        st.info(f"**Consejo:** Revisa las operaciones perdedoras. ¿Hay patrones? Podrías ajustar el `stop_loss` para ser más estricto o añadir filtros de entrada adicionales para evitar operaciones de baja probabilidad.")
                    elif win_rate < 40 and avg_pnl_per_trade > 0:
                        st.warning(f"La estrategia {strategy_name} es rentable a pesar de una baja tasa de acierto, lo que indica que sus operaciones ganadoras son muy grandes.")
                        st.info(f"**Consejo:** Enfócate en mejorar la tasa de acierto sin sacrificar el tamaño de las ganancias. Podrías refinar los criterios de entrada o buscar confirmaciones adicionales antes de operar.")
                    else:
                        st.error(f"La estrategia {strategy_name} está generando pérdidas. Necesita una revisión urgente.")
                        st.info(f"**Consejo:** Analiza a fondo las operaciones perdedoras. Revisa los parámetros de `stop_loss` y `take_profit` en `config.json`. Considera ajustar los indicadores o añadir nuevos filtros para evitar entradas en condiciones de mercado desfavorables. Si el ratio R/R es bajo, busca formas de aumentarlo.")
                    
                    if avg_risk_reward < 1.0 and avg_pnl_per_trade > 0:
                        st.warning(f"Aunque la estrategia {strategy_name} es rentable, su ratio Riesgo/Recompensa promedio ({avg_risk_reward:.2f}) es bajo. Esto significa que arriesgas más de lo que ganas en promedio.")
                        st.info(f"**Consejo:** Intenta ajustar el `take_profit` para que sea mayor en relación con el `stop_loss`. Un ratio R/R de 1.5 o 2.0 es generalmente deseable.")
                    elif avg_risk_reward >= 1.5:
                        st.success(f"La estrategia {strategy_name} tiene un buen ratio Riesgo/Recompensa promedio ({avg_risk_reward:.2f}), lo que es excelente para la gestión de capital.")

            # --- Detalle de Operaciones ---
            with st.expander("Ver Operaciones Individuales", expanded=True):
                if total_trades > 0:
                    for index, trade in strat_trades.iterrows():
                        trade_pnl = trade['profit_loss']
                        pnl_color = "green" if trade_pnl > 0 else "red" if trade_pnl < 0 else "gray"
                        
                        with st.container(border=True):
                            col1, col2 = st.columns(2)
                            with col1:
                                st.markdown(f"**ID Operación:** {trade.get('dealId', 'N/A')}")
                                st.markdown(f"**Instrumento:** {trade.get('epic', 'N/A')}")
                                st.markdown(f"**Dirección:** {trade.get('signal', 'N/A')}")
                                st.markdown(f"**Tamaño:** {trade.get('size', 'N/A')}")
                                st.markdown(f"**Estado:** {trade.get('status', 'N/A')}")
                                st.markdown(f"**Razón de Cierre:** {trade.get('exit_reason', 'N/A')}")
                            with col2:
                                st.markdown(f"**P/L:** <span style='color:{pnl_color};'>{trade_pnl:+.2f}</span>", unsafe_allow_html=True)
                                st.markdown(f"**Apertura:** {trade['open_time'].strftime('%Y-%m-%d %H:%M:%S')}")
                                st.markdown(f"**Cierre:** {trade['close_time'].strftime('%Y-%m-%d %H:%M:%S')}")
                                st.markdown(f"**Precio Entrada:** {trade.get('entry_price', 0.0):.2f}")
                                st.markdown(f"**Precio Cierre:** {trade.get('close_price', 0.0):.2f}")
                                st.markdown(f"**Stop Loss:** {trade.get('stop_loss', 0.0):.2f}")
                                st.markdown(f"**Take Profit:** {trade.get('take_profit', 0.0):.2f}")

                            # Mostrar detalles adicionales si existen
                            details_expander = st.expander("Más Detalles")
                            with details_expander:
                                st.write(f"**Referencia:** {trade.get('dealReference', 'N/A')}")
                                st.write(f"**Regla SL=TP contra Tendencia Activa:** {'Sí' if trade.get('tp_sl_against_trend_active', False) else 'No'}")
                                
                                entry_cond = trade.get('entry_conditions', '{}')
                                exit_cond = trade.get('exit_conditions', '{}')
                                
                                try:
                                    entry_cond_dict = json.loads(entry_cond.replace('''''', '"'))
                                    st.write("**Condiciones de Entrada:**")
                                    st.json(entry_cond_dict)
                                except (json.JSONDecodeError, TypeError):
                                    st.write(f"**Condiciones de Entrada:** {entry_cond}")

                                try:
                                    exit_cond_dict = json.loads(exit_cond.replace('''''', '"'))
                                    st.write("**Condiciones de Salida:**")
                                    st.json(exit_cond_dict)
                                except (json.JSONDecodeError, TypeError):
                                    st.write(f"**Condiciones de Salida:** {exit_cond}")
                else:
                    st.info("No hay operaciones para mostrar.")

            st.divider()
    else:
        st.info("No hay operaciones cerradas para el análisis detallado de estrategias.")

@st.dialog("Historial de Operaciones", width="large")
def show_trade_history(df_history):
    st.subheader("Historial Completo de Operaciones")
    if not df_history.empty:
        st.dataframe(df_history, use_container_width=True)
    else:
        st.info("No hay historial de operaciones para mostrar.")

@st.dialog("Análisis con IA (Gemini)")
def show_ai_analysis(bot):
    if bot:
        with st.spinner("Generando análisis con IA... Esto puede tardar un momento."):
            analysis = bot.get_ai_analysis()
        st.markdown(analysis, unsafe_allow_html=True)
    else:
        st.warning("El bot no está inicializado. Conéctate primero.")

def mcp_agent_ui():
    st.subheader("Control del Agente MCP")
    st.write("Gestiona el estado y las operaciones del Agente de Control Maestro (MCP).")

    bot_is_running = False
    try:
        with open(TradingBot.BOT_STATUS_FILE, "r") as f:
            status_data = json.load(f)
            bot_is_running = status_data.get("is_running", False)
    except (FileNotFoundError, json.JSONDecodeError):
        pass # El archivo no existe o está vacío, el bot no está corriendo

    col1, col2 = st.columns(2)

    with col1:
        st.write("### Estado del Agente")
        if bot_is_running:
            st.success("Agente MCP Activo")
            if st.button("Detener Agente MCP"):
                # Enviar señal para detener el bot (escribir en el archivo de estado)
                try:
                    with open(TradingBot.BOT_STATUS_FILE, "w") as f:
                        json.dump({"is_running": False}, f)
                    st.success("Señal de detención enviada al bot.")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al enviar señal de detención: {e}")
        else:
            st.error("Agente MCP Inactivo")
            if st.button("Iniciar Agente MCP"):
                # Enviar señal para iniciar el bot (escribir en el archivo de estado)
                try:
                    with open(TradingBot.BOT_STATUS_FILE, "w") as f:
                        json.dump({"is_running": True}, f)
                    st.success("Señal de inicio enviada al bot.")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al enviar señal de inicio: {e}")

    with col2:
        st.write("### Estrategias Activas")
        config_data = load_config() # Recargar la configuración para obtener el estado más reciente
        active_strategies_from_config = []
        for strategy_name, strategy_config in config_data.items():
            if isinstance(strategy_config, dict) and strategy_config.get("is_active", False):
                active_strategies_from_config.append(strategy_name)

        if active_strategies_from_config:
            for strategy in sorted(active_strategies_from_config):
                st.write(f"- {strategy}")
        else:
            st.info("No hay estrategias activas en la configuración.")

    st.write("---")
    st.write("### Logs del Agente")
    log_container = st.container(height=300)
    log_lines = read_bot_logs()
    log_container.code('\n'.join(log.strip() for log in log_lines), language='log')





# --- Lógica Principal ---
st.set_page_config(layout="wide")

# Inicializar el estado de la sesión para la configuración si no existe
if 'config' not in st.session_state:
    st.session_state.config = load_config()

# Inicializar el bot si no está ya inicializado en el estado de la sesión
if 'bot_initialized' not in st.session_state:
    st.session_state.bot_initialized = False
if 'bot' not in st.session_state:
    st.session_state.bot = None
if 'capital_client_api' not in st.session_state:
    st.session_state.capital_client_api = None
if 'binance_client_api' not in st.session_state:
    st.session_state.binance_client_api = None
if 'listener' not in st.session_state:
    st.session_state.listener = None

st.sidebar.title("Modo de Operación")
mode = st.sidebar.radio("Selecciona el modo:", ('Análisis (Offline)', 'Live (Bot Activo)'), key='dashboard_mode', index=1)

if mode == 'Live (Bot Activo)' and not st.session_state.bot_initialized:
    logger.debug(f"Attempting to initialize bot. mode={mode}, bot_initialized={st.session_state.bot_initialized}")
    with st.spinner('Inicializando y conectando el bot...'):
        if initialize_bot():
            st.session_state.bot_initialized = True
            st.success("¡Bot inicializado y conectado con éxito!")
            time.sleep(1)
            st.rerun()
        else:
            st.error("Fallo al inicializar el bot. Revisa los logs para más detalles.")

# --- Renderizado de la UI ---
df_history = load_trade_history() # Cargar datos una vez
col1, col2 = st.columns([1, 2])

tab1, tab2, tab3, tab4 = st.tabs(["Análisis", "Live", "Configuración", "MCP Agent"])

with tab4:
    st.header("Control del Agente de Control Maestro (MCP)")
    mcp_agent_ui()

with col1:
    sync_ui()
    bot_controls_ui()
    manage_strategies_ui()
    strategy_parameters_ui()

    st.divider()
    st.subheader("Análisis")
    
    if not df_history.empty:
        closed_trades = df_history[df_history['status'] == 'CLOSED']
        if st.button("Rendimiento por Estrategia"):
            show_performance_by_strategy(closed_trades)

        if st.button("Análisis Detallado de Estrategias"):
            show_detailed_strategy_analysis(closed_trades)
        
        if st.button("Operaciones"):
            show_trade_history(df_history)

        if st.button("Análisis con IA (Gemini)"):
            show_ai_analysis(st.session_state.get('bot'))
    else:
        st.info("No hay historial de operaciones para analizar.")


with col2:
    if mode == 'Live (Bot Activo)':
        st.header("Controles y Señales (Live)")
        st.info("El bot de trading se ejecuta en segundo plano. Usa los controles en la pestaña 'MCP Agent' para gestionar su estado y ver los logs.")
        
        # Mostrar el estado actual del bot (activo/inactivo)
        bot_is_running = False
        try:
            with open(TradingBot.BOT_STATUS_FILE, "r") as f:
                status_data = json.load(f)
                bot_is_running = status_data.get("is_running", False)
        except (FileNotFoundError, json.JSONDecodeError):
            pass # El archivo no existe o está vacío, el bot no está corriendo

        if bot_is_running:
            st.success("El bot de trading está ACTIVO.")
        else:
            st.error("El bot de trading está INACTIVO.")

        st.markdown("--- ")
        st.subheader("Resumen de Estrategias")
        
        # Placeholder para el resumen detallado de estrategias
        strategy_status_placeholder = st.empty()

        # Actualizar el resumen de estrategias periódicamente
        if st.session_state.get('bot'):
            status_message = st.session_state.bot.get_detailed_strategy_status()
            strategy_status_placeholder.markdown(status_message, unsafe_allow_html=True)
        else:
            strategy_status_placeholder.info("El resumen detallado solo está disponible en modo 'Live (Bot Activo)'.")
        
        # Forzar una recarga del dashboard cada 5 segundos para actualizar el estado
        time.sleep(5)
        st.rerun()

    st.divider()
    st.header("Análisis de Operaciones")

    if not df_history.empty:
        closed_trades = df_history[df_history['status'] == 'CLOSED']
        total_pnl = closed_trades['profit_loss'].sum()
        win_rate = (closed_trades[closed_trades['profit_loss'] > 0].shape[0] / closed_trades.shape[0] * 100) if not closed_trades.empty else 0
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Operaciones Cerradas", closed_trades.shape[0])
        c2.metric("P/L Total", f"{total_pnl:+.2f}")
        c3.metric("Tasa de Acierto", f"{win_rate:.2f}%")

    else:
        st.info("No hay historial de operaciones para analizar.")