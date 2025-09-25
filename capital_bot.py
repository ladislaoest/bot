import logging
import queue
import logging
logging.basicConfig(level=logging.DEBUG) # Habilitar logging detallado para librerías de terceros

import json
import inspect
import glob
from strategies.utils import normalize_strategy_result
import requests
import time
import os
import re
from dotenv import load_dotenv
load_dotenv()
import importlib.util
from datetime import datetime, timedelta
import pandas as pd
import ta
import threading
import csv
from pycoingecko import CoinGeckoAPI
import logging.handlers

import numpy as np
from utils.klines_utils import normalize_klines
from utils.indicators import add_ema
from mcp_agent import MCPAgent

from binance_websocket_client import BinanceWebsocketClient

config_lock = threading.Lock()

# Configuración del logger
LOG_FILE = "bot_logs.log"
logger = logging.getLogger("TradingBotLogger")
logger.setLevel(logging.DEBUG)

# Handler para escribir en archivo
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(file_handler)

# Handler para consola
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

# Configuración del logger para decisiones de la IA
AI_LOG_FILE = "ai_decisions.log"
ai_logger = logging.getLogger("AIDecisionLogger")
ai_logger.setLevel(logging.DEBUG)

# Handler para escribir en archivo de decisiones de la IA
ai_file_handler = logging.handlers.RotatingFileHandler(
    AI_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
ai_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
ai_logger.addHandler(ai_file_handler)

def sanitize_for_json(data):
    """Convierte recursivamente los tipos de NumPy a tipos nativos de Python para la serialización JSON."""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(i) for i in data]
    elif isinstance(data, np.bool_):
        return bool(data)
    elif isinstance(data, np.integer):
        return int(data)
    elif isinstance(data, np.floating):
        return float(data)
    return data

def parse_float(value_str):
    if isinstance(value_str, (int, float)):
        return float(value_str)
    if isinstance(value_str, str):
        # Remove currency symbols, thousands separators, and replace comma with a dot
        cleaned_str = re.sub(r'[^\d,.-]', '', value_str).replace(',', '.')
        try:
            return float(cleaned_str)
        except (ValueError, TypeError):
            return 0.0
    return 0.0

def get_default_strategy_params(strategy_class):
    signature = inspect.signature(strategy_class.__init__)
    params = {"is_active": True}  # Default to active
    for name, param in signature.parameters.items():
        if name == 'self' or name == 'config':
            continue
        if param.default is not inspect.Parameter.empty:
            params[name] = param.default
    return params

def load_config(config_file="config.json"):
    with config_lock:
        try:
            with open(config_file, 'r') as f:
                content = f.read()
                if not content:
                    return {}
                return json.loads(content)
        except FileNotFoundError:
            logger.warning(f"ADVERTENCIA: No se encontró el archivo {config_file}. Se usarán los valores por defecto de cada estrategia.")
            return {}
        except json.JSONDecodeError:
            logger.warning(f"ADVERTENCIA: Error al decodificar {config_file}. Se usarán los valores por defecto.")
            return {}

def load_strategy_classes(strategy_dir="strategies"):
    logger.debug(f"Loading strategy classes from {strategy_dir}.")
    strategy_classes = {}
    strategy_files = glob.glob(os.path.join(strategy_dir, "*.py"))
    logger.debug(f"Archivos de estrategia encontrados: {strategy_files}") # DEBUG PRINT
    for strategy_file in strategy_files:
        module_name = os.path.splitext(os.path.basename(strategy_file))[0]
        spec = importlib.util.spec_from_file_location(module_name, strategy_file)
        if spec and spec.loader:
            strategies_module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(strategies_module)
                for name, obj in inspect.getmembers(strategies_module, inspect.isclass):
                    if hasattr(obj, 'run') and obj.__module__ == strategies_module.__name__ and name != "BaseStrategy": # Añadido: y no es BaseStrategy
                        strategy_classes[name] = obj # Return the class, not an instance
                        logger.debug(f"Clase de estrategia cargada: {name}") # DEBUG PRINT
            except Exception as e:
                logger.error(f"Error al cargar '{strategy_file}': {e}")
    return strategy_classes

class CapitalComAPIClient: # (No changes in this class)
    def __init__(self, account_id=None): # Añadir account_id como parámetro opcional
        self.base_url = os.getenv("CAPITAL_BASE_URL")
        self.api_key = os.getenv("CAPITAL_API_KEY")
        self.identifier = os.getenv("CAPITAL_IDENTIFIER")
        self.password = os.getenv("CAPITAL_API_PASSWORD")
        self.session_file = "session.json"
        self.session = requests.Session()
        self.cst_token, self.x_security_token = None, None
        self.account_id = account_id # Usar el parámetro account_id
        logger.debug(f"CapitalComAPIClient inicializado con account_id: {self.account_id}") # DEBUG PRINT
        if not all([self.base_url, self.api_key, self.identifier, self.password]): raise ValueError("Faltan credenciales en .env")
        if not self._load_session(): self._authenticate()
    def _load_session(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r') as f: tokens = json.load(f)
                self.cst_token, self.x_security_token = tokens.get('cst_token'), tokens.get('x_security_token')
                if self.cst_token and self.x_security_token: return True
            except (IOError, json.JSONDecodeError): pass
        return False
    def _save_session(self):
        try:
            with open(self.session_file, 'w') as f: json.dump({'cst_token': self.cst_token, 'x_security_token': self.x_security_token}, f)
        except IOError: pass
    def _test_authentication(self):
        try: return self._make_authenticated_request("GET", "/accounts") is not None
        except Exception: return False
    def _authenticate(self):
        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }

        login_data = {
            "identifier": self.identifier,
            "password": self.password,
            "encryptedPassword": False
        }

        response = requests.post(f"{self.base_url}/session", json=login_data, headers=headers, timeout=10)

        if response.status_code != 200:
            raise Exception(f"Error de autenticación: {response.status_code} - {response.text}")

        self.cst_token = response.headers.get("CST")
        self.x_security_token = response.headers.get("X-SECURITY-TOKEN")

        logger.debug(f"Autenticación exitosa. CST: {self.cst_token[:5]}..., X-SECURITY-TOKEN: {self.x_security_token[:5]}...") # DEBUG PRINT
        logger.debug(f"account_id en _authenticate después de autenticación: {self.account_id}") # DEBUG PRINT
        self._save_session()

    def _set_active_account(self):
        if not self.account_id:
            logger.warning("ADVERTENCIA: No se ha especificado un account_id para establecer como activo.")
            return

        headers = {
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst_token,
            "X-SECURITY-TOKEN": self.x_security_token,
            "Content-Type": "application/json"
        }
        data = {"accountId": self.account_id}
        
        try:
            response = self.session.request("PUT", f"{self.base_url}/session", headers=headers, json=data, timeout=10)
            response.raise_for_status()
            logger.debug(f"Cuenta activa establecida a {self.account_id} exitosamente.")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400 and e.response.json().get("errorCode") == "error.not-different.accountId":
                logger.debug(f"La cuenta {self.account_id} ya es la cuenta activa. No es necesario cambiarla.")
            else:
                logger.error(f"ERROR HTTP al establecer cuenta activa: {e.response.status_code} - {e.response.text}")
                raise Exception(f"Error al establecer la cuenta activa: {e.response.status_code} - {e.response.text}")
    def _make_authenticated_request(self, method, endpoint, params=None, data=None, is_retry=False):
        if not self.cst_token or not self.x_security_token: self._authenticate()
        headers = {"X-CAP-API-KEY": self.api_key, "CST": self.cst_token, "X-SECURITY-TOKEN": self.x_security_token}
        try:
            response = self.session.request(method, f"{self.base_url}{endpoint}", headers=headers, params=params, json=data, timeout=10)
            response.raise_for_status()
            
            if endpoint == "/markets":
                logger.debug(f"Raw response for /markets: {response.text}") # DEBUG PRINT

            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"ERROR HTTP en _make_authenticated_request: {e.response.status_code} - {e.response.text}") # Added detailed error logging
            if e.response.status_code in (401, 403) and not is_retry: self._authenticate(); return self._make_authenticated_request(method, endpoint, params, data, True)
            raise
    def get_market_data(self, epic):
        return self._make_authenticated_request("GET", f"/markets/{epic}")

    def get_all_markets(self):
        """
        Obtiene una lista de todos los mercados disponibles en Capital.com.
        API: GET /markets
        """
        return self._make_authenticated_request("GET", "/markets")

    def get_accounts(self):
        """
        Obtiene una lista de todas las cuentas asociadas al usuario autenticado.
        API: GET /accounts
        """
        return self._make_authenticated_request("GET", "/accounts")

    def get_open_positions(self): return self._make_authenticated_request("GET", "/positions")
    def get_transaction_history(self, from_date): return self._make_authenticated_request("GET", "/history/transactions", params={'from': from_date})
    
    def place_market_order(self, epic, direction, size, stop_level=None, profit_level=None):
        logger.debug(f"Llamada a place_market_order - Epic: {epic}, Direction: {direction}, Size: {size}")
        order_data = {"epic": epic, "direction": direction, "size": size, "accountId": self.account_id}
        
        if stop_level is not None:
            order_data["stopLevel"] = round(stop_level, 2)
        if profit_level is not None:
            order_data["profitLevel"] = round(profit_level, 2)

        logger.debug(f"Datos de la orden a enviar: {order_data}")
        response = self._make_authenticated_request("POST", "/positions", data=order_data)
        logger.debug(f"Respuesta de la API de Capital.com: {response}")
        return response

    def amend_position(self, deal_id, new_stop_level, new_profit_level):
        logger.debug(f"Intentando modificar posición {deal_id} con SL: {new_stop_level:.2f}, TP: {new_profit_level:.2f}")
        new_stop_level = round(new_stop_level, 2)
        new_profit_level = round(new_profit_level, 2)
        amend_data = {"stopLevel": new_stop_level, "profitLevel": new_profit_level}
        logger.debug(f"Datos de modificación enviados para {deal_id}: {amend_data}")
        response = self._make_authenticated_request("PUT", f"/positions/{deal_id}", data=amend_data)
        logger.debug(f"Respuesta de Capital.com al modificar posición {deal_id}: {response}")
        return response

    def close_position(self, deal_id):
        """
        Cierra una posición abierta en Capital.com.
        API: DELETE /positions/{dealId}
        """
        return self._make_authenticated_request("DELETE", f"/positions/{deal_id}")

class BinanceAPIClient:
    def __init__(self, api_key, api_secret):
        from binance.client import Client
        self.client = Client(api_key, api_secret)
    def get_historical_klines(self, symbol, interval, limit):
        klines = self.client.get_historical_klines(symbol, interval, limit=limit)
        # Devolver un formato más estándar con claves 'open_time', 'open', 'high', 'low', 'close', 'volume'
        return {'prices': [{"open_time": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in klines]}

class CoinGeckoAPIClient:
    def __init__(self, api_key):
        self.cg = CoinGeckoAPI(api_key=api_key)

    def get_historical_data(self, coin_id, vs_currency, days, interval='daily'):
        try:
            ohlc_data = self.cg.get_coin_ohlc_by_id(id=coin_id, vs_currency=vs_currency, days=days)
            
            prices = []
            for ohlc in ohlc_data:
                prices.append({
                    "open": ohlc[1],
                    "high": ohlc[2],
                    "low": ohlc[3],
                    "close": ohlc[4],
                    "volume": 0 
                })
            return {'prices': prices}
        except Exception as e:
            logger.error(f"Error al obtener datos históricos de CoinGecko para {coin_id}: {e}")
            return {'prices': []}

class TelegramListener:
    def __init__(self, trading_bot):
        self.bot = trading_bot
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.running = False
        self.update_id = 0
    def start(self):
        if not self.bot_token: return
        self._clear_webhook()
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Oyente de Telegram iniciado.")
    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join()
    def _clear_webhook(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/setWebhook?url="
        try:
            return requests.get(url, timeout=10).json().get('ok')
        except Exception: pass
    def _get_updates(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.update_id + 1}&timeout=5"
        try:
            return requests.get(url, timeout=10).json().get('result', [])
        except Exception: return []
    def _get_commands_message(self):
        commands_list = [
            "/list: Muestra el estado actual de todas las estrategias.",
            "/resume<numero>: Reanuda una estrategia específica.",
            "/pause<numero>: Pausa una estrategia específica.",
            "/pause_all: Pausa todas las estrategias activas.",
            "/resume_all: Activa todas las estrategias disponibles.",
            "/niv<1-10>: Cambia el nivel de agresividad (1=Conservador, 10=Agresivo).",
            "/com: Muestra esta lista de comandos.",
            "/estado: Estado Detallado de Estrategias.",
            "/historial: Historial de Operaciones.",
            "/resumen: Resumen de Rendimiento y Rendimiento por Estrategia.",
            "/analisis: Análisis de IA.",
            "/status: Muestra si el bot está activo o parado.",
            "/start: Inicia el bot.",
            "/stop: Detiene el bot."
        ]
        escaped_list = [s.replace('<', '&lt;').replace('>', '&gt;') for s in commands_list]
        return "<b>Comandos de Telegram disponibles:</b>\n\n" + "\n".join(escaped_list)

    def _process_updates(self, updates):
        for update in updates:
            self.update_id = update['update_id']
            if 'message' not in update or 'text' not in update['message']: continue
            text = update['message']['text'].strip()

            command_map = {
                "/list": (self.bot.get_numbered_status, "HTML"),
                "/com": (self._get_commands_message, None),
                "/estado": (self.bot.get_detailed_strategy_status, "HTML"),
                "/historial": (self.bot.get_trade_history, "HTML"),
                "/resumen": (self.bot.get_performance_summary, "HTML"),
                "/analisis": (self.bot.get_ai_analysis, "HTML"),
                "/status": (self.bot.get_app_status, "HTML"),
                "/start": (self.bot.start_app, None),
                "/stop": (self.bot.stop_app, None),
                "/pause_all": (self.bot.pause_all_strategies, None),
                "/resume_all": (self.bot.resume_all_strategies, None)
            }

            response_message = None
            parse_mode_arg = None
            command_tuple = command_map.get(text)

            if command_tuple:
                command_func, parse_mode_arg = command_tuple
                response_message = command_func()
            else:
                match_action = re.match(r"/(resume|pause)(\d+)", text)
                match_level = re.match(r"/niv(\d+)", text)
                match_size = re.match(r"/size\s+([0-9.]+)", text)
                match_close_strategy = re.match(r"/close_strategy\s+(.+)", text)

                if match_action:
                    action, number = match_action.groups()
                    sorted_strategies = sorted(self.bot.available_strategies.keys())
                    if 1 <= int(number) <= len(sorted_strategies):
                        strategy_name = sorted_strategies[int(number) - 1]
                        response_message = self.bot.resume_strategy(strategy_name) if action == "resume" else self.bot.pause_strategy(strategy_name)
                    else:
                        response_message = f"Número de estrategia inválido: {number}."
                elif match_level:
                    level = int(match_level.group(1))
                    if 1 <= level <= 10:
                        self.bot.config["global_settings"]["aggressiveness_level"] = level
                        with config_lock:
                            with open("config.json", "w") as f:
                                json.dump(self.bot.config, f, indent=2)
                        
                        self.bot.aggressiveness_level = level
                        self.bot.reload_all_strategy_configs()
                        response_message = f"Nivel de agresividad cambiado a {level}. Estrategias recargadas."
                    else:
                        response_message = f"Nivel de agresividad inválido: {level}. Usa un número del 1 al 10."
                elif match_size:
                    try:
                        new_size = float(match_size.group(1))
                        if new_size > 0:
                            response_message = self.bot.set_global_order_size(new_size)
                        else:
                            response_message = "El tamaño de lote debe ser un número positivo."
                    except ValueError:
                        response_message = "Formato de tamaño de lote inválido. Usa /size <valor> (ej. /size 0.0015)."
                elif match_close_strategy:
                    strategy_name = match_close_strategy.group(1)
                    response_message = self.bot.close_trades_for_strategy(strategy_name)
                else:
                    response_message = "Comando no reconocido."
            
            self.bot._send_telegram_notification(response_message, parse_mode=parse_mode_arg)
    def _run_loop(self):
        while self.running:
            updates = self._get_updates()
            if updates: self._process_updates(updates)
            time.sleep(2)



class TradingBot:
    TRADE_HISTORY_FIELDNAMES = [
        "open_time", "strategy", "epic", "direction", "size",
        "entry_price", "stop_level", "profit_level",
        "dealReference", "dealId", "status", "profit_loss",
        "close_time", "close_price", "entry_conditions",
        "exit_conditions", "exit_reason", "tp_sl_against_trend_active", "sl_moved_to_be", "break_even_profit_pct",
        "current_trend", "atr_5m"
    ]

    BOT_STATUS_FILE = "bot_status.json"

    def _send_telegram_notification(self, message, parse_mode=None):
        logger.debug(f"Attempting to send Telegram message: {message}")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env file.")
            return
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.debug("Telegram message sent successfully.")
        except requests.exceptions.RequestException as e:
            logger.error(f"--- ERROR TELEGRAM: No se pudo enviar la notificación: {e} ---")

    def _save_status(self):
        try:
            with open(self.BOT_STATUS_FILE, "w") as f:
                json.dump({"is_running": self.running}, f)
        except Exception as e:
            logger.error(f"Error al guardar el estado del bot: {e}")

    def __init__(self, capital_client_api, binance_client_api):
        logger.debug("TradingBot.__init__ called.") # New line
        if os.path.exists(self.BOT_STATUS_FILE):
            os.remove(self.BOT_STATUS_FILE)
            logger.info(f"Archivo de estado {self.BOT_STATUS_FILE} eliminado para forzar inicio activo.")

        load_dotenv()
        coingecko_api_key = os.getenv("COINGECKO_API_KEY")
        self.coingecko_client_api = CoinGeckoAPIClient(api_key=coingecko_api_key)
        self.capital_client_api, self.binance_client_api = capital_client_api, binance_client_api
        self.running = False
        self.last_trade_time = {}
        self.strategy_signals = {}
        self.config = load_config()
        self.aggressiveness_level = self.config.get("global_settings", {}).get("aggressiveness_level", 3)
        self.enable_tp_sl_against_trend = self.config.get("global_settings", {}).get("enable_tp_sl_against_trend", False)
        self.enable_two_tp_trades = self.config.get("global_settings", {}).get("enable_two_tp_trades", False)
        self.global_order_size = self.config.get("global_settings", {}).get("global_order_size", 0.0015)
        self.prevent_counter_trend_trades = self.config.get("global_settings", {}).get("prevent_counter_trend_trades", True)
        self.mcp_agent = MCPAgent(self.capital_client_api, self.binance_client_api, self.config)
        accounts = self.capital_client_api.get_accounts()
        self.account_id = None
        target_account_name = "bot"
        logger.debug(f'Cuentas detectadas: {[ "{0} (ID: {1})".format(acc.get("accountName"), acc.get("accountId")) for acc in accounts.get("accounts", [])]}')
        selected_account_id = None
        normalized_target_account_name = target_account_name.strip().lower()
        for account in accounts.get('accounts', []):
            normalized_account_name = account.get('accountName', '').strip().lower()
            if normalized_account_name == normalized_target_account_name:
                selected_account_id = account.get('accountId')
                break
        
        if selected_account_id is None:
            raise ValueError(f"No se pudo encontrar la cuenta '{target_account_name}'. Cuentas disponibles: {[acc.get('accountName') for acc in accounts.get('accounts', [])]}.")
        
        self.account_id = selected_account_id
        logger.debug(f"self.account_id final: {self.account_id}")
        self.capital_client_api.account_id = self.account_id
        self.capital_client_api._set_active_account()
        all_markets = self.capital_client_api.get_all_markets()
        self.btc_epic = None
        for market in all_markets.get('markets', []):
            if market.get('instrumentName') == "Bitcoin/USD":
                self.btc_epic = market.get('epic')
                break
        
        if not self.btc_epic:
            raise ValueError("No se pudo encontrar el EPIC para BTC/USD en Capital.com. Asegúrate de que el símbolo sea correcto o que el mercado esté disponible.")

        self.available_strategies = {}
        strategy_classes = load_strategy_classes("strategies")
        for name, strategy_class in strategy_classes.items():
            strategy_config = self.config.get(name, {})
            aggressiveness = strategy_config.get("aggressiveness_level", self.aggressiveness_level)
            self.available_strategies[name] = strategy_class(strategy_config, aggressiveness_level=aggressiveness)

            # Inicializar self.strategy_signals para cada estrategia
            self.strategy_signals[name] = {"signal": "HOLD", "message": "Inicializando...", "detailed_status": {}} # Añadido

            if name not in self.config:
                default_params = get_default_strategy_params(strategy_class)
                self.config[name] = default_params
                logger.debug(f"Parámetros por defecto para {name}: {default_params}")
        
        with config_lock:
            with open("config.json", "w") as f:
                json.dump(self.config, f, indent=2)
        
        self.active_strategies = {}
        for name, instance in self.available_strategies.items():
            if self.config.get(name, {}).get("is_active", True):
                self.active_strategies[name] = instance
        self.csv_lock, self.trade_history_file = threading.Lock(), "trade_history.csv"
        self.signals_lock = threading.Lock() # New line
        self.open_trades = {} # NEW
        self.opening_trade = {}
        self.strategy_types = {
            "LadisLong": "scalping",
            "LadisLongLite": "scalping",
            "Sabado": "scalping",
            "SabadoLite": "scalping",
            "ScalpingEmaRsi": "scalping",
            "SheilalongLite": "scalping",
            "SheilashortLite": "scalping",
            "LateralReversal": "scalping",
            "estrategia1corto": "scalping", # Assuming this is also scalping
            "GabinalongShort": "scalping", # Assuming this is also scalping
            "Guillermoshort": "scalping", # Assuming this is also scalping
        }
        self.kline_queues = {
            '1m': queue.Queue(),
            '5m': queue.Queue(),
            '30m': queue.Queue()
        }
        self.binance_ws_clients = {}
        self.klines_data = {interval: [] for interval in self.kline_queues.keys()}
        for interval in self.kline_queues.keys():
            # Pre-fill with historical data
            try:
                initial_klines = self.binance_client_api.get_historical_klines("BTCUSDT", interval, limit=1000).get("prices", [])
                self.klines_data[interval].extend(initial_klines)
            except Exception as e:
                logger.error(f"Error al precargar datos históricos para {interval}: {e}")

            ws_client = BinanceWebsocketClient(symbol="BTCUSDT", interval=interval, data_queue=self.kline_queues[interval])
            ws_client.start()
            self.binance_ws_clients[interval] = ws_client

        if not os.path.exists(self.trade_history_file):
            with open(self.trade_history_file, "w", newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.TRADE_HISTORY_FIELDNAMES)
                writer.writeheader()
        self.monitor_cooldown, self.last_monitor_time = timedelta(seconds=60), datetime.min
        logger.debug(f"DEBUG: self.running antes de start_polling(): {self.running}") # DEBUG PRINT
        self.start_polling()

    def set_strategy(self, strategy_name, enable=True):
        if enable:
            if strategy_name in self.available_strategies and strategy_name not in self.active_strategies:
                self.active_strategies[strategy_name] = self.available_strategies[strategy_name]
                return f"Estrategia '{strategy_name}' activada."
            return f"Estrategia '{strategy_name}' ya estaba activa o no existe."
        else:
            if strategy_name in self.active_strategies:
                del self.active_strategies[strategy_name]
                with self.signals_lock: # New line
                    if strategy_name in self.strategy_signals: del self.strategy_signals[strategy_name]
                return f"Estrategia '{strategy_name}' desactivada."
            return f"Estrategia '{strategy_name}' no estaba activa."

    def pause_strategy(self, n): return self.set_strategy(n, enable=False)
    def resume_strategy(self, n): return self.set_strategy(n, enable=True)
    def pause_all_strategies(self):
        for name in list(self.active_strategies.keys()): self.pause_strategy(name)
        return "Todas las estrategias han sido pausadas."
    def resume_all_strategies(self):
        for name in self.available_strategies: self.resume_strategy(name)
        return "Todas las estrategias han sido activadas."

    def get_numbered_status(self):
        status = "<b>Estado de Estrategias</b>\n\n"
        for i, name in enumerate(sorted(self.available_strategies.keys()), 1):
            is_active = "🟢 Activa" if name in self.active_strategies else "🔴 Pausada"
            status += f"{i}. {name}: {is_active}\n"
        return status

    def get_detailed_strategy_status(self):
        status_message = "<b>Estado Detallado de Estrategias</b><br><br>" # Cambiado \n\n a <br><br>
        for name, instance in sorted(self.available_strategies.items()):
            is_active = "🟢 Activa" if name in self.active_strategies else "🔴 Pausada"
            status_message += f"<b>Estrategia:</b> {name} ({is_active})<br>" # Cambiado \n a <br>

            strategy_detail = None
            with self.signals_lock:
                if name in self.strategy_signals:
                    strategy_detail = self.strategy_signals[name].copy()

            if strategy_detail:
                strategy_detail = self.strategy_signals[name]
                status_message += f"  <b>Última Señal:</b> {strategy_detail.get('signal', 'N/A')}<br>" # Cambiado \n a <br>
                status_message += f"  <b>Mensaje:</b> {strategy_detail.get('message', 'N/A')}<br><br>" # Cambiado \n\n a <br><br>
            else:
                status_message += "  <i>No hay información de la última ejecución.</i><br><br>" # Cambiado \n\n a <br><br>
        return status_message

    def reload_all_strategy_configs(self):
        logger.debug("Reloading all strategy configs.")
        self.config = load_config()
        self.aggressiveness_level = self.config.get("global_settings", {}).get("aggressiveness_level", self.aggressiveness_level)

        new_available_strategies = {}
        strategy_classes = load_strategy_classes("strategies")
        for name, strategy_class in strategy_classes.items():
            strategy_config = self.config.get(name, {})
            logger.debug(f"Recargando estrategia {name} con config: {strategy_config}")
            aggressiveness = strategy_config.get("aggressiveness_level", self.aggressiveness_level)
            new_available_strategies[name] = strategy_class(strategy_config, aggressiveness_level=aggressiveness)
        
        self.available_strategies = new_available_strategies

        new_active_strategies = {}
        for name, instance in self.active_strategies.items():
            if name in self.available_strategies:
                new_active_strategies[name] = self.available_strategies[name]
        self.active_strategies = new_active_strategies
        logger.info("Configuración de estrategias recargada.")

    def delete_strategy(self, strategy_name):
        if strategy_name in self.active_strategies:
            del self.active_strategies[strategy_name]
            if strategy_name in self.strategy_signals:
                del self.strategy_signals[strategy_name]

        if strategy_name in self.config:
            del self.config[strategy_name]
            with config_lock:
                with open("config.json", "w") as f:
                    json.dump(self.config, f, indent=2)

        strategy_file_path = os.path.join("strategies", f"{strategy_name}.py")
        if os.path.exists(strategy_file_path):
            os.remove(strategy_file_path)

        self.reload_all_strategy_configs()
        return f"Estrategia '{strategy_name}' eliminada correctamente."

    def clear_trade_history(self):
        with self.csv_lock:
            if os.path.exists(self.trade_history_file):
                os.remove(self.trade_history_file)
            with open(self.trade_history_file, "w", newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.TRADE_HISTORY_FIELDNAMES)
                writer.writeheader()
        return "Historial de operaciones limpiado y reseteado."

    def get_trade_history(self, limit=5):
        history_message = "<b>Historial de Operaciones Recientes</b>\n\n"
        try:
            with self.csv_lock:
                if not os.path.exists(self.trade_history_file) or os.path.getsize(self.trade_history_file) == 0:
                    return history_message + "No hay operaciones registradas aún."
                
                df = pd.read_csv(self.trade_history_file, on_bad_lines='skip')
                if df.empty:
                    return history_message + "No hay operaciones registradas aún."
                
                df['open_time'] = pd.to_datetime(df['open_time'])
                df = df.sort_values(by='open_time', ascending=False)
                
                for index, row in df.head(limit).iterrows():
                    status_emoji = "✅" if row['status'] == 'CLOSED' and row['profit_loss'] > 0 else ("❌" if row['status'] == 'CLOSED' and row['profit_loss'] <= 0 else "⏳")
                    profit_loss_str = f"{row['profit_loss']:.2f}" if pd.notna(row['profit_loss']) else "N/A"
                    
                    history_message += f"{status_emoji} <b>{row['strategy']}</b> ({row['direction']}) - {row['epic']}\n"
                    history_message += f"  Apertura: {row['open_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                    history_message += f"  Estado: {row['status']}\n"
                    if row['status'] == 'CLOSED':
                        history_message += f"  P/L: {profit_loss_str}\n"
                    history_message += "\n"
                
                return history_message
        except Exception as e:
            return history_message + f"Error al cargar el historial: {e}"

    def get_performance_summary(self):
        summary_message = "<b>Resumen de Rendimiento</b>\n\n"
        try:
            with self.csv_lock:
                if not os.path.exists(self.trade_history_file) or os.path.getsize(self.trade_history_file) == 0:
                    return summary_message + "No hay operaciones registradas para analizar."
                
                df = pd.read_csv(self.trade_history_file, on_bad_lines='skip')
                df = df[df['status'] == 'CLOSED'].copy()
                
                if df.empty:
                    return summary_message + "No hay operaciones cerradas para analizar."
                
                total_pnl = df['profit_loss'].sum()
                total_trades = len(df)
                winning_trades = df[df['profit_loss'] > 0]
                win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
                
                summary_message += f"<b>Rendimiento General:</b>\n"
                summary_message += f"  P/L Total: {total_pnl:.2f}\n"
                summary_message += f"  Total Operaciones Cerradas: {total_trades}\n"
                summary_message += f"  Tasa de Acierto: {win_rate:.2f}%\n\n"
                
                summary_message += "<b>Rendimiento por Estrategia:</b>\n"
                performance_by_strategy = df.groupby('strategy')['profit_loss'].agg(['sum', 'count', lambda x: (x > 0).sum()]).reset_index()
                performance_by_strategy.columns = ['strategy', 'total_pnl', 'trade_count', 'win_count']
                
                for index, row in performance_by_strategy.iterrows():
                    strategy_win_rate = (row['win_count'] / row['trade_count'] * 100) if row['trade_count'] > 0 else 0
                    summary_message += f"  <b>{row['strategy']}:</b>\n"
                    summary_message += f"    P/L: {row['total_pnl']:.2f}\n"
                    summary_message += f"    Operaciones: {row['trade_count']}\n"
                    summary_message += f"    Tasa de Acierto: {strategy_win_rate:.2f}%\n"
                
                return summary_message
        except Exception as e:
            return summary_message + f"Error al generar resumen de rendimiento: {e}"

    def get_ai_analysis(self):
        ai_analysis_message = "<b>Análisis de IA</b>\n\n"
        try:
            with self.csv_lock:
                if not os.path.exists(self.trade_history_file) or os.path.getsize(self.trade_history_file) == 0:
                    return ai_analysis_message + "No hay operaciones registradas para analizar."
                df = pd.read_csv(self.trade_history_file, on_bad_lines='skip')

            df.fillna("N/A", inplace=True)

            trade_history_simplified = []
            cols_to_include = ['strategy', 'open_time', 'direction', 'entry_price', 'close_price', 'profit_loss', 'status', 'exit_reason']
            
            for col in cols_to_include:
                if col not in df.columns:
                    df[col] = "N/A"

            for index, row in df.tail(20).iterrows():
                entry_price = f"{row['entry_price']:.2f}" if isinstance(row['entry_price'], (int, float)) else row['entry_price']
                close_price = f"{row['close_price']:.2f}" if isinstance(row['close_price'], (int, float)) else row['close_price']
                profit_loss = f"{row['profit_loss']:.2f}" if isinstance(row['profit_loss'], (int, float)) else row['profit_loss']
                trade_info = (
                    f"Estrategia: {row['strategy']}, "
                    f"Apertura: {row['open_time']}, "
                    f"Dirección: {row['direction']}, "
                    f"Entrada: {entry_price}, "
                    f"Cierre: {close_price}, "
                    f"P/L: {profit_loss}, "
                    f"Estado: {row['status']}, "
                    f"Razón Cierre: {row['exit_reason']}"
                )
                trade_history_simplified.append(trade_info)
            
            trade_history_csv = "\n".join(trade_history_simplified)

            performance_summary = self.get_performance_summary()
            strategy_configs = json.dumps(self.config, indent=2)
            
            open_positions_df = df[df['status'] == 'OPEN']
            open_positions_simplified = []
            for index, row in open_positions_df.tail(20).iterrows():
                entry_price = f"{row['entry_price']:.2f}" if isinstance(row['entry_price'], (int, float)) else row['entry_price']
                trade_info = (
                    f"Estrategia: {row['strategy']}, "
                    f"Apertura: {row['open_time']}, "
                    f"Dirección: {row['direction']}, "
                    f"Entrada: {entry_price}"
                )
                open_positions_simplified.append(trade_info)
            open_positions_info = "\n".join(open_positions_simplified) if open_positions_simplified else "Ninguna"

            market_data = self.capital_client_api.get_market_data(epic=self.btc_epic)
            current_price = market_data.get('snapshot', {}).get('bid', 'N/A')

            # Add strategy source code to the prompt
            strategy_source_code = ""
            for name, instance in self.active_strategies.items():
                strategy_file_path = os.path.join("strategies", f"{name}.py")
                if os.path.exists(strategy_file_path):
                    with open(strategy_file_path, "r", encoding="utf-8") as f:
                        strategy_source_code += f"--- Código de la estrategia: {name} ---\n"
                        strategy_source_code += f.read()
                        strategy_source_code += "\n\n"

            prompt = f"""Eres un analista de trading experto y muy detallado para un bot de criptomonedas. Tu objetivo es proporcionar un análisis exhaustivo y actionable para mejorar el rendimiento del bot, analizando hasta el último detalle de cada estrategia. Sé extremadamente específico y justificado en tus consejos.

            Basado en los siguientes datos, genera un reporte en español con el siguiente formato:
            1.  <b>Diagnóstico General del Bot</b>: Un párrafo conciso sobre el rendimiento general del bot (ganando, perdiendo, en equilibrio), destacando tendencias clave.
            2.  <b>Análisis Detallado por Estrategia</b>:
                Para CADA estrategia, proporciona:
                a.  <b>Rendimiento Observado</b>: Resumen de su P/L, número de operaciones, tasa de acierto.
                b.  <b>Análisis de Entrada (Condiciones y Parámetros)</b>: Describe cómo entra la estrategia, analizando sus parámetros actuales y cómo estos influyen en las señales. Si es posible, infiere qué condiciones de mercado (tendencia, volatilidad, momentum) favorecen o perjudican sus entradas.
                c.  <b>Análisis de Salida (SL/TP)</b>: Evalúa la gestión de riesgo (SL/TP) de la estrategia. ¿Son adecuados para su estilo? ¿Se tocan con demasiada frecuencia o muy poco? Sugiere ajustes si es necesario.
                d.  <b>Consejos de Optimización</b>: Propón cambios de parámetros específicos y justificados para mejorar su rendimiento. Por ejemplo: \"Para 'Estrategia X', considera aumentar el periodo de la EMA de 50 a 100 para reducir señales falsas en mercados volátiles\" o \"El Take Profit de 'Estrategia Y' en 0.3% es demasiado ambicioso para un scalp, prueba a reducirlo a 0.18%\". Si la estrategia cae en falsas rupturas o se comporta mal en mercados laterales, explica por qué y cómo ajustar los parámetros para mitigar esto.
            3.  <b>Tácticas para Operaciones Abiertas</b>: Si hay operaciones abiertas, da una recomendación táctica para cada una (ej. \"La operación de 'Estrategia Z' está cerca del Take Profit, mantener\" o \"La operación de 'Estrategia W' está en ligera pérdida, considera mover el Stop Loss a precio de entrada si el mercado se vuelve en contra\").

            --- DATOS ---
            <b>Precio Actual de BTC/USD:</b> {current_price}

            <b>Resumen de Rendimiento General y por Estrategia:</b>
            {performance_summary}

            <b>Configuración Actual de Parámetros por Estrategia:</b>
            {strategy_configs}

            <b>Historial Completo de Operaciones:</b>
            {trade_history_csv}

            <b>Operaciones Actualmente Abiertas:</b>
            {open_positions_info}

            <b>Código Fuente de las Estrategias Activas:</b>
            {strategy_source_code}
            --- FIN DE DATOS ---

            Genera el análisis usando exclusivamente los datos proporcionados. Usa formato HTML (<b>, <i>, \n, <br>) para la respuesta. Asegúrate de que el análisis sea muy detallado y cubra todos los puntos solicitados para cada estrategia, incluso si el rendimiento es nulo o bajo. Si no hay datos suficientes para un punto, indícalo explícitamente. Prioriza la claridad y la estructura en tu respuesta.
            """
            
            # Save the prompt to a file for debugging
            try:
                with open("gemini_prompt.txt", "w", encoding="utf-8") as f:
                    f.write(prompt)
            except Exception as e:
                logger.error(f"Error al escribir el prompt de Gemini en el archivo: {e}")

            logger.debug(f"Prompt para Gemini: {prompt}")

            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                return "<b>Análisis de IA</b>\n\nError: La clave GEMINI_API_KEY no está configurada en el archivo .env"

            api_url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_api_key}"
            headers = {'Content-Type': 'application/json'}
            data = {"contents": [{"parts": [{"text": prompt}]}]}

            response = requests.post(api_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()

            result = response.json()
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            return f"<b>Análisis de IA (Gemini)</b>\n\n{content}"

        except Exception as e:
            return ai_analysis_message + f"Error al generar análisis de IA: {e}"

    def manage_open_trade(self, trade, strategy_type):
        try:
            logger.debug(f"Gestionando operación abierta: {trade['dealId']}")
            decision_data = self.get_ai_trade_management_decision(trade.to_dict(), strategy_type)

            if not decision_data:
                logger.warning(f"No se pudo obtener una decisión de la IA para la operación {trade['dealId']}. No se tomarán acciones.")
                return

            decision = decision_data.get("decision")
            reason = decision_data.get("reason")
            ai_logger.info(f"Decisión de la IA para {trade['dealId']}: {decision}. Razón: {reason}")

            if decision == "ADJUST_SLTP":
                sl_multiplier = decision_data.get("sl_multiplier")
                tp_multiplier = decision_data.get("tp_multiplier")
                if sl_multiplier and tp_multiplier:
                    sl_pct = (sl_multiplier * trade["atr_5m"] / trade['entry_price']) * 100
                    tp_pct = (tp_multiplier * trade["atr_5m"] / trade['entry_price']) * 100
                    
                    if trade["direction"] == "BUY":
                        final_sl = trade['entry_price'] * (1 - sl_pct / 100)
                        final_tp = trade['entry_price'] * (1 + tp_pct / 100)
                    else: # SELL
                        final_sl = trade['entry_price'] * (1 + sl_pct / 100)
                        final_tp = trade['entry_price'] * (1 - tp_pct / 100)

                    self.capital_client_api.amend_position(trade['dealId'], final_sl, final_tp)
                    ai_logger.info(f"Posición {trade['dealId']} modificada. Nuevo SL: {final_sl:.2f}, Nuevo TP: {final_tp:.2f}")
                else:
                    ai_logger.warning(f"La IA decidió ajustar SL/TP para {trade['dealId']} pero no proporcionó nuevos multiplicadores.")

            elif decision == "CLOSE":
                self.capital_client_api.close_position(trade['dealId'])
                ai_logger.info(f"Posición {trade['dealId']} cerrada por decisión de la IA.")

        except Exception as e:
            logger.error(f"Error al gestionar la operación abierta {trade['dealId']}: {e}")

    def get_ai_trade_management_decision(self, trade_data, strategy_type):
        try:
            prompt = f"""Eres un experto en gestión de riesgos para un bot de trading de criptomonedas. Tu tarea es decidir si mantener, ajustar el SL/TP o cerrar una operación abierta, basándote en los datos de la operación, las condiciones actuales del mercado y el tipo de estrategia.

            **Instrucciones:**
            1.  Analiza los datos de la operación abierta.
            2.  Decide la mejor acción: "HOLD" (mantener), "ADJUST_SLTP" (ajustar SL/TP), or "CLOSE" (cerrar).
            3.  Si la decisión es "ADJUST_SLTP", proporciona los nuevos `sl_multiplier` y `tp_multiplier`.
            4.  Proporciona una breve `reason` para tu decisión.
            5.  Devuelve la decisión en formato JSON.

            **Datos de la Operación:**
            {json.dumps(trade_data, indent=2)}
            -   **Tipo de Estrategia:** {strategy_type}

            **Formato de Salida (JSON):**
            ```json
            {{
                "decision": "<HOLD|ADJUST_SLTP|CLOSE>",
                "reason": "<Tu razonamiento aquí>",
                "sl_multiplier": <nuevo_valor_si_ajustas>,
                "tp_multiplier": <nuevo_valor_si_ajustas>
            }}
            ```
            """

            ai_logger.info(f"Prompt para decisión de gestión de trade:\n{prompt}")

            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                logger.error("GEMINI_API_KEY no encontrada en .env")
                return None

            api_url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_api_key}"
            headers = {'Content-Type': 'application/json'}
            data = {"contents": [{"parts": [{"text": prompt}]}]}

            response = requests.post(api_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()

            result = response.json()
            content = result['candidates'][0]['content']['parts'][0]['text']
            ai_logger.info(f"Respuesta de la IA para decisión de gestión de trade:\n{content}")
            
            json_match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                decision = json.loads(json_str)
                return decision
            else:
                logger.error(f"No se pudo encontrar JSON en la respuesta de Gemini para gestión de trade: {content}")
                return None

        except Exception as e:
            logger.error(f"Error al obtener decisión de gestión de trade de la IA: {e}")
            return None

    def get_ai_sl_tp(self, result_queue, strategy_name, direction, entry_price, current_trend, atr_5m, strategy_type):
        try:
            prompt = f"""Eres un experto en gestión de riesgos para un bot de trading de criptomonedas. Tu tarea es determinar los multiplicadores de Stop Loss (SL) y Take Profit (TP) para una operación específica, basados en la estrategia, las condiciones del mercado, la volatilidad y el tipo de estrategia.

            **Instrucciones:**
            1.  Analiza los datos de la operación.
            2.  Determina los multiplicadores `sl_multiplier` y `tp_multiplier` óptimos.
            3.  Devuelve los multiplicadores en formato JSON.

            **Datos de la Operación:**
            -   **Estrategia:** {strategy_name}
            -   **Tipo de Estrategia:** {strategy_type}
            -   **Dirección:** {direction}
            -   **Precio de Entrada:** {entry_price}
            -   **Tendencia Actual (30m):** {current_trend}
            -   **ATR (5m):** {atr_5m}

            **Consideraciones:**
            -   Para estrategias de scalping, los multiplicadores deben ser más ajustados.
            -   Si la operación es contra la tendencia principal, considera un `tp_multiplier` más conservador.
            -   El `sl_multiplier` debe ser lo suficientemente amplio para evitar ser activado por el ruido del mercado, pero lo suficientemente ajustado para limitar las pérdidas.

            **Formato de Salida (JSON):**
            ```json
            {{
                "sl_multiplier": <valor>,
                "tp_multiplier": <valor>
            }}
            ```
            """

            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                logger.error("GEMINI_API_KEY no encontrada en .env")
                result_queue.put((None, None))
                return

            api_url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_api_key}"
            headers = {'Content-Type': 'application/json'}
            data = {"contents": [{"parts": [{"text": prompt}]}]}

            response = requests.post(api_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()

            result = response.json()
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            # Extract the JSON from the response
            json_match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                multipliers = json.loads(json_str)
                result_queue.put((multipliers.get("sl_multiplier"), multipliers.get("tp_multiplier")))
            else:
                logger.error(f"No se pudo encontrar JSON en la respuesta de Gemini para SL/TP: {content}")
                result_queue.put((None, None))

        except Exception as e:
            logger.error(f"Error al obtener SL/TP de la IA: {e}")
            result_queue.put((None, None))

    def _get_binance_klines_data(self, symbol, interval, limit):
        # Get new klines from the queue
        while not self.kline_queues[interval].empty():
            kline = self.kline_queues[interval].get()
            
            # Convert kline to the same format as historical klines
            formatted_kline = {
                "open_time": kline['t'],
                "open": float(kline['o']),
                "high": float(kline['h']),
                "low": float(kline['l']),
                "close": float(kline['c']),
                "volume": float(kline['v'])
            }

            # Append the new kline to the historical data
            self.klines_data[interval].append(formatted_kline)

            # Keep the list of klines at a reasonable size
            if len(self.klines_data[interval]) > 2000:
                self.klines_data[interval].pop(0)

        # Return the last `limit` klines
        return {'prices': self.klines_data[interval][-limit:]}

    def get_historical_data_from_binance(self, symbol, interval, limit):
        try:
            klines_data = self.binance_client_api.get_historical_klines(symbol=symbol, interval=interval, limit=limit)
            return klines_data
        except Exception as e:
            logger.error(f"ERROR al obtener datos de Binance para {symbol}-{interval}-{limit}: {e}")
            return {}

    def get_app_status(self):
        if self.running:
            return "El bot está <b>ACTIVO</b> y operando."
        else:
            return "El bot está <b>PARADO</b>."

    def start_polling(self):
        if self.running: return
        self.running = True
        self._save_status()
        try:
            threading.Thread(target=self._polling_loop, daemon=True).start()
            logger.debug("DEBUG: Hilo de polling intentado iniciar.") # DEBUG PRINT
        except Exception as e:
            logger.error(f"ERROR: No se pudo iniciar el hilo de polling: {e}")

    def stop_polling(self):
        self.running = False
        self._save_status()

    def start_app(self):
        if self.running:
            return "El bot ya está activo."
        self.start_polling()
        return "Bot iniciado correctamente."

    def stop_app(self):
        if not self.running:
            return "El bot ya está parado."
        self.stop_polling()
        return "Bot detenido correctamente."

    def set_global_order_size(self, new_size):
        with config_lock:
            self.config["global_settings"]["global_order_size"] = new_size
            with open("config.json", "w") as f:
                json.dump(self.config, f, indent=2)
        self.global_order_size = new_size
        return f"Tamaño de orden global actualizado a {new_size}."

    def close_trades_for_strategy(self, strategy_name):
        try:
            open_positions = self.capital_client_api.get_open_positions().get('positions', [])
            closed_trades = []
            for position in open_positions:
                if position.get('position', {}).get('strategy') == strategy_name:
                    deal_id = position.get('position', {}).get('dealId')
                    self.capital_client_api.close_position(deal_id)
                    closed_trades.append(deal_id)
            if closed_trades:
                return f"Cerradas {len(closed_trades)} operaciones para la estrategia {strategy_name}: {', '.join(closed_trades)}"
            else:
                return f"No se encontraron operaciones abiertas para la estrategia {strategy_name}."
        except Exception as e:
            logger.error(f"Error al cerrar operaciones para la estrategia {strategy_name}: {e}")
            return f"Error al cerrar operaciones para la estrategia {strategy_name}."

    def has_open_trade(self, strategy_name):
        with self.signals_lock:
            return strategy_name in self.open_trades and self.open_trades[strategy_name]

    def _monitor_open_positions(self):
        dtype_spec = {'dealId': 'object', 'close_time': 'object', 'close_price': 'object', 'profit_loss': 'object', 'entry_conditions': 'object', 'exit_conditions': 'object', 'exit_reason': 'object', 'tp_sl_against_trend_active': 'bool'}
        open_trades = pd.DataFrame()
        try:
            with self.csv_lock:
                if not os.path.exists(self.trade_history_file) or os.path.getsize(self.trade_history_file) == 0:
                    with open(self.trade_history_file, "w", newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=self.TRADE_HISTORY_FIELDNAMES)
                        writer.writeheader()
                    trades_df = pd.DataFrame(columns=self.TRADE_HISTORY_FIELDNAMES)
                else:
                    try:
                        trades_df = pd.read_csv(self.trade_history_file, dtype=dtype_spec, on_bad_lines='skip')
                        if 'break_even_profit_pct' not in trades_df.columns: trades_df['break_even_profit_pct'] = 0.0
                        if 'sl_moved_to_be' not in trades_df.columns: trades_df['sl_moved_to_be'] = False
                        if 'tp_sl_against_trend_active' not in trades_df.columns: trades_df['tp_sl_against_trend_active'] = False
                        trades_df['sl_moved_to_be'] = trades_df['sl_moved_to_be'].fillna(False).astype(bool)
                        trades_df['tp_sl_against_trend_active'] = trades_df['tp_sl_against_trend_active'].replace({np.nan: False}).astype(bool)
                    except pd.errors.EmptyDataError:
                        with open(self.trade_history_file, "w", newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=self.TRADE_HISTORY_FIELDNAMES)
                            writer.writeheader()
                        trades_df = pd.DataFrame(columns=self.TRADE_HISTORY_FIELDNAMES)
            open_trades = trades_df[(trades_df['status'] == 'OPEN') & (trades_df['dealId'].notna())]
            logger.debug(f"open_trades (from CSV):\n{open_trades}")
            if open_trades.empty: return

            if self.config.get("global_settings", {}).get("enable_ai_trade_management", False):
                for index, trade in open_trades.iterrows():
                    self.manage_open_trade(trade, self.strategy_types.get(trade["strategy"], "desconocido"))

            api_positions = self.capital_client_api.get_open_positions().get('positions', [])
            logger.debug(f"api_positions (from API): {api_positions}")

            open_api_deal_ids = set()
            for pos in api_positions:
                if 'position' in pos and 'dealId' in pos['position']:
                    open_api_deal_ids.add(pos['position']['dealId'])
            
            closed_trades_deal_ids = set(open_trades['dealId']) - open_api_deal_ids
            logger.debug(f"closed_trades_deal_ids: {closed_trades_deal_ids}")

            if not closed_trades_deal_ids: return

            from_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S')
            transactions = self.capital_client_api.get_transaction_history(from_date).get('transactions', [])
            updates_made = False
            for deal_id in closed_trades_deal_ids:
                logger.debug(f"Procesando deal_id en closed_trades_deal_ids: {deal_id}")
                for trx in transactions:
                    if trx.get('dealId') == deal_id and trx.get('note') == 'Trade closed':
                        pnl = parse_float(trx.get('size', '0.0'))
                        currency = trx.get('currency', '')
                        close_time = trx.get('date')
                        logger.debug(f"Transacción de cierre: {trx}")
                        close_price = parse_float(trx.get('price', pd.NA))
                        
                        trade_row = trades_df[trades_df['dealId'] == deal_id].iloc[0]
                        strategy_name = trade_row['strategy']
                        trade_epic = trade_row['epic']
                        trade_direction = trade_row['direction']
                        stop_level = trade_row['stop_level']
                        profit_level = trade_row['profit_level']

                        exit_reason = "Manual/Other"
                        if pd.notna(close_price) and pd.notna(stop_level) and pd.notna(profit_level):
                            if abs(close_price - stop_level) < 0.01 * stop_level:
                                exit_reason = "Stop Loss"
                            elif abs(close_price - profit_level) < 0.01 * profit_level:
                                exit_reason = "Take Profit"

                        instance = self.available_strategies.get(strategy_name)
                        exit_conditions = self._get_current_detailed_status(trade_epic, "BTCUSDT", trade_direction, instance)

                        trades_df.loc[trades_df['dealId'] == deal_id, ['status', 'profit_loss', 'close_time', 'close_price', 'exit_conditions', 'exit_reason']] = ['CLOSED', pnl, close_time, close_price, json.dumps(sanitize_for_json(exit_conditions)), exit_reason]
                        updates_made = True
                        
                        self._send_telegram_notification(f"<b>Operación Cerrada ({strategy_name})</b>\n" \
                                                   f"<b>Deal ID:</b> {deal_id}\n" \
                                                   f"<b>Resultado:</b> {pnl:+.2f} {currency}\n" \
                                                   f"<b>Precio Cierre:</b> {close_price:.2f}\n" \
                                                   f"<b>Motivo:</b> {exit_reason}")
                        with self.signals_lock:
                            if strategy_name in self.open_trades and deal_id in self.open_trades[strategy_name]:
                                self.open_trades[strategy_name].remove(deal_id)
                        break
            if updates_made:
                with self.csv_lock:
                    trades_df.to_csv(self.trade_history_file, index=False)
        except Exception as e:
            logger.error(f"--- [MONITOR] ERROR CRÍTICO durante el ciclo de monitoreo: {e} ---")

    def _process_new_trade(self, deal_reference, strategy_name, direction, size, sl_pct, tp_pct, current_trend, atr_5m):
        try:
            if self.enable_tp_sl_against_trend and \
               ((direction == "BUY" and current_trend == "bearish") or \
                (direction == "SELL" and current_trend == "bullish")):
                logger.debug(f"Aplicando regla SL=TP para {direction} en tendencia {current_trend}.")
                tp_pct = sl_pct
            
            timeout, start_time = 120, time.time()
            deal_id, real_entry_price = None, None
            confirmation = None
            while time.time() - start_time < timeout:
                confirmation = self.capital_client_api._make_authenticated_request("GET", f"/confirms/{deal_reference}")
                if confirmation.get('status') == 'OPEN' and confirmation.get('affectedDeals'):
                    deal_info = confirmation['affectedDeals'][0]
                    deal_id = deal_info['dealId']
                    real_entry_price = confirmation.get('level')
                    break
                time.sleep(2)

            if not deal_id or not real_entry_price:
                logger.error(f"ERROR: No se pudo confirmar la operación {deal_reference} a tiempo.")
                return

            with self.signals_lock:
                if strategy_name not in self.open_trades:
                    self.open_trades[strategy_name] = []
                self.open_trades[strategy_name].append(deal_id)

            if direction == "BUY":
                final_sl = real_entry_price * (1 - sl_pct)
                final_tp = real_entry_price * (1 + tp_pct)
            else: # SELL
                final_sl = real_entry_price * (1 + sl_pct)
                final_tp = real_entry_price * (1 - tp_pct)

            amend_response = self.capital_client_api.amend_position(deal_id, final_sl, final_tp)
            if amend_response and "dealReference" in amend_response:
                logger.debug(f"Amend OK para {strategy_name}: {amend_response}")
                logger.debug(f"Posición {deal_id} modificada con SL: {final_sl:.2f} y TP: {final_tp:.2f}")

                open_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self._send_telegram_notification(
                    f"<b>Orden Abierta ({strategy_name})</b>\n" \
                    f"<b>Hora:</b> {open_time_str}\n" \
                    f"<b>Dirección:</b> {direction}\n" \
                    f"<b>Precio Entrada Real:</b> {real_entry_price:.2f}\n" \
                    f"<b>Deal ID:</b> {deal_id}"
                )
            else:
                logger.error(f"ERROR: No se pudo modificar la posición {deal_id} con SL/TP. Respuesta: {amend_response}")
                self._send_telegram_notification(
                    f"<b>ADVERTENCIA: No se pudo establecer SL/TP para la operación {deal_id} ({strategy_name})</b>\n" \
                    f"Revisa manualmente la posición en Capital.com."
                )

            trade_info = {
                "open_time": open_time_str, "strategy": strategy_name, "epic": self.btc_epic, 
                "direction": direction, "size": size, "entry_price": real_entry_price, 
                "stop_level": final_sl, "profit_level": final_tp, "dealReference": deal_reference, 
                "dealId": deal_id, "status": "OPEN", "profit_loss": pd.NA, "close_time": pd.NA, 
                "close_price": pd.NA, "entry_conditions": json.dumps(sanitize_for_json(self.strategy_signals[strategy_name].get("detailed_status", {}))), 
                "exit_conditions": "", "exit_reason": "",
                "tp_sl_against_trend_active": self.enable_tp_sl_against_trend,
                "sl_moved_to_be": False,
                "break_even_profit_pct": 0.0,
                "current_trend": current_trend,
                "atr_5m": atr_5m
            }
            with self.csv_lock:
                file_exists = os.path.isfile(self.trade_history_file)
                with open(self.trade_history_file, "a", newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=self.TRADE_HISTORY_FIELDNAMES)
                    if not file_exists: writer.writeheader()
                    writer.writerow(trade_info)

        except Exception as e:
            logger.error(f"--- ERROR en _process_new_trade para {deal_reference}: {e} ---")
        finally:
            self.opening_trade[strategy_name] = False

    def _polling_loop(self):
        print("DEBUG: _polling_loop iniciado.") # DEBUG PRINT
        cooldown = timedelta(minutes=5)
        while self.running:
            try:
                logger.info("--- INICIO CICLO POLLING ---")
                now = datetime.now()
                if now - self.last_monitor_time > self.monitor_cooldown:
                    self._monitor_open_positions()
                
                market_data = self.capital_client_api.get_market_data(epic=self.btc_epic)['snapshot']
                current_bid = market_data.get('bid')
                current_offer = market_data.get('offer')
                if not current_bid or not current_offer:
                    logger.error("ERROR: No se pudo obtener el precio de mercado para el cálculo preliminar de SL/TP.")
                    time.sleep(20)
                    continue
                
                preliminary_price = (current_bid + current_offer) / 2

                klines_30m = self._get_binance_klines_data("BTCUSDT", "30m", limit=50).get("prices", [])
                df_30m = normalize_klines(klines_30m, min_length=30)
                
                current_trend = "neutral"
                if not df_30m.empty:
                    df_30m = add_ema(df_30m, 30)
                    latest_30m = df_30m.iloc[-1]
                    if latest_30m["close"] > latest_30m["EMA30"]:
                        current_trend = "bullish"
                    elif latest_30m["close"] < latest_30m["EMA30"]:
                        current_trend = "bearish"
                
                logger.debug(f"Tendencia actual (30m): {current_trend}")
                
                for name, instance in list(self.active_strategies.items()):
                    logger.debug(f"Procesando estrategia: {name}")
                    if self.opening_trade.get(name):
                        logger.debug(f"Estrategia '{name}' ya tiene una operación en proceso de apertura. Saltando.")
                        continue

                    if self.has_open_trade(name):
                        logger.debug(f"Estrategia '{name}' ya tiene una operación abierta. Saltando.")
                        continue

                    if self.last_trade_time.get(name) and (now - self.last_trade_time[name]) < cooldown: continue
                    
                    print(f"DEBUG: Llamando a {name}.run()") # DEBUG PRINT
                    try:
                        raw_result = instance.run(self.capital_client_api, self)
                        result = normalize_strategy_result(raw_result)
                        logger.debug(f"Resultado normalizado para {name}: {result}") # Añadido
                        with self.signals_lock:
                            self.strategy_signals[name] = result
                        signal_text = self.strategy_signals[name].get("signal", "HOLD")
                        direction = None
                        if signal_text.startswith("BUY"): direction = "BUY"
                        elif signal_text.startswith("SELL"): direction = "SELL"

                        if direction:
                            # Intentar obtener sl_pct y tp_pct directamente de la estrategia
                            sl_pct = result.get("sl_pct")
                            tp_pct = result.get("tp_pct")

                            # Si la estrategia no proporcionó SL/TP válidos, usar la IA
                            if sl_pct is None or tp_pct is None or sl_pct == 0.0 or tp_pct == 0.0:
                                result_queue = queue.Queue()
                                atr_5m_value = self.strategy_signals[name].get("detailed_status", {}).get("ATR", 0.0)
                                ai_thread = threading.Thread(target=self.get_ai_sl_tp, args=(result_queue, name, direction, preliminary_price, current_trend, atr_5m_value, self.strategy_types.get(name, "desconocido")))
                                ai_thread.start()

                                try:
                                    sl_multiplier, tp_multiplier = result_queue.get(timeout=10) # 10 second timeout
                                except queue.Empty:
                                    logger.warning(f"Timeout al esperar la respuesta de la IA para {name}. Usando los valores de config.json")
                                    sl_multiplier, tp_multiplier = None, None

                                if sl_multiplier is None or tp_multiplier is None:
                                    logger.warning(f"No se pudieron obtener los multiplicadores de la IA para {name}. Usando los valores de config.json")
                                    sl_multiplier = instance.sl_multiplier
                                    tp_multiplier = instance.tp_multiplier

                                sl_pct = (sl_multiplier * atr_5m_value / preliminary_price)
                                tp_pct = (tp_multiplier * atr_5m_value / preliminary_price)

                            if sl_pct is None or tp_pct is None or sl_pct == 0.0 or tp_pct == 0.0:
                                logger.error(f"Estrategia '{name}' devolvió 'sl_pct' o 'tp_pct' inválidos (None o 0.0). Saltando trade.")
                                self.strategy_signals[name]['signal'] = "ERROR"
                                self.strategy_signals[name]['message'] = "La estrategia devolvió sl_pct o tp_pct inválidos"
                                continue
                                
                            if self.prevent_counter_trend_trades:
                                if (direction == "BUY" and current_trend == "bearish"):
                                    logger.info(f"Trade de COMPRA bloqueado para '{name}' por tendencia principal bajista.")
                                    self.strategy_signals[name]['signal'] = "HOLD"
                                    self.strategy_signals[name]['message'] = f"Trade de COMPRA bloqueado por tendencia principal bajista."
                                    continue
                                if (direction == "SELL" and current_trend == "bullish"):
                                    logger.info(f"Trade de VENTA bloqueado para '{name}' por tendencia principal alcista.")
                                    self.strategy_signals[name]['signal'] = "HOLD"
                                    self.strategy_signals[name]['message'] = f"Trade de VENTA bloqueado por tendencia principal alcista."
                                    continue

                            apply_sl_tp_against_trend_rule = self.enable_tp_sl_against_trend and \
                               ((direction == "BUY" and current_trend == "bearish") or \
                                (direction == "SELL" and current_trend == "bullish"))

                            def open_single_trade(tp_modifier=0, atr_5m=None):
                                if self.opening_trade.get(name):
                                    return False
                                
                                self.opening_trade[name] = True
                                current_tp_pct = tp_pct + tp_modifier
                                if apply_sl_tp_against_trend_rule:
                                    current_tp_pct = sl_pct

                                if direction == "BUY":
                                    preliminary_sl = preliminary_price * (1 - sl_pct / 100)
                                    preliminary_tp = preliminary_price * (1 + current_tp_pct / 100)
                                else: # SELL
                                    preliminary_sl = preliminary_price * (1 + sl_pct / 100)
                                    preliminary_tp = preliminary_price * (1 - current_tp_pct / 100)

                                logger.debug(f"preliminary_price: {preliminary_price}, sl_pct: {sl_pct}, tp_pct: {current_tp_pct}, preliminary_sl: {preliminary_sl}, preliminary_tp: {preliminary_tp}")

                                response = self.capital_client_api.place_market_order(
                                    self.btc_epic, direction, self.global_order_size,
                                    stop_level=preliminary_sl, profit_level=preliminary_tp
                                )

                                if "dealReference" in response:
                                    self.last_trade_time[name] = now
                                    threading.Thread(target=self._process_new_trade, args=(
                                        response["dealReference"], name, direction, self.global_order_size,
                                        sl_pct, current_tp_pct, current_trend, atr_5m
                                    )).start()
                                    return True
                                else:
                                    self.opening_trade[name] = False
                                    logger.error(f"ERROR: No se pudo abrir la operación para {name} (TP mod: {tp_modifier}).")
                                    return False

                            if apply_sl_tp_against_trend_rule or not self.enable_two_tp_trades:
                                open_single_trade(atr_5m=atr_5m_value)
                            else:
                                open_single_trade(atr_5m=atr_5m_value)
                                time.sleep(1)
                                open_single_trade(tp_modifier=-0.10, atr_5m=atr_5m_value)

                    except Exception as strategy_e:
                        logger.error(f"ERROR: La estrategia '{name}' falló durante la ejecución: {strategy_e}")
                        with self.signals_lock: # New line
                            self.strategy_signals[name] = {"signal": "ERROR", "message": f"Error: {strategy_e}"}
                    finally:
                        self.opening_trade[name] = False

            except Exception as e:
                logger.error(f"ERROR CRÍTICO en el bucle de polling: {e}")
            logger.info("--- FIN CICLO POLLING ---")
            logger.info(f"Estado actual de strategy_signals: {self.strategy_signals}") # Añadido para depuración
            time.sleep(20)

    def _get_current_detailed_status(self, epic, binance_symbol, direction, instance):
        detailed_status = {}
        try:
            market_data = self.capital_client_api.get_market_data(epic=epic)['snapshot']
            current_price = market_data.get('bid') if direction == "BUY" else market_data.get('offer')
            detailed_status["current_price"] = current_price

            ema_period = getattr(instance, 'ema_period', getattr(instance, 'ema_fast', 20))
            rsi_period = getattr(instance, 'rsi_period', 14)

            klines_data = self.binance_client_api.get_historical_klines(symbol=binance_symbol, interval="1m", limit=ema_period + 10).get("prices", [])
            df_klines = normalize_klines(klines_data, min_length=ema_period + 5)

            if not df_klines.empty:
                df_klines = add_ema(df_klines, period=ema_period)
        except Exception as e:
            logger.error(f"Error getting detailed status: {e}")
        return detailed_status