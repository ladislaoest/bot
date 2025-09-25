import os
import json
import requests
import pandas as pd
import ta
import logging
from datetime import datetime, timedelta
import inspect
import re

# Assuming these are available in the project root or via sys.path

from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi # Example indicators

logger = logging.getLogger(__name__)

class MCPAgent:
    def __init__(self, capital_client, binance_client, config: dict):
        self.capital_client = capital_client
        self.binance_client = binance_client
        self.config = config
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY no está configurada en el archivo .env")
        
        self.tools = self._register_tools()
        self.is_running = False # Nuevo atributo para controlar el estado de ejecución del agente

    def start(self):
        self.is_running = True
        logger.info("Agente MCP iniciado.")

    def stop(self):
        self.is_running = False
        logger.info("Agente MCP detenido.")

    def _register_tools(self):
        """Registers the available tools for the LLM."""
        tools = {
            "get_market_data": self.get_market_data,
            "get_indicators": self.get_indicators,
            "place_order": self.place_order,
            "get_portfolio": self.get_portfolio,
            "risk_check": self.risk_check,
        }
        return tools

    def _call_llm(self, prompt: str) -> str:
        """Sends a prompt to the Gemini API and returns the response."""
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={self.gemini_api_key}"
        headers = {'Content-Type': 'application/json'}
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        
        try:
            response = requests.post(api_url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al llamar a la API de Gemini: {e}")
            return f"ERROR: Fallo en la comunicación con el LLM: {e}"
        except (KeyError, IndexError) as e:
            logger.error(f"Error al parsear la respuesta del LLM: {e}, Respuesta: {result}")
            return f"ERROR: Fallo al parsear la respuesta del LLM: {e}"

    def _execute_tool(self, tool_name: str, **kwargs):
        """Executes a registered tool."""
        tool = self.tools.get(tool_name)
        if not tool:
            return f"ERROR: Herramienta '{tool_name}' no encontrada."
        
        try:
            # Validate arguments against tool's signature
            sig = inspect.signature(tool)
            # Filter out 'self' from parameters for binding
            tool_params = [p for name, p in sig.parameters.items() if name != 'self']
            
            # Create a new signature without 'self' for binding
            new_sig = inspect.Signature(tool_params)
            bound_args = new_sig.bind(**kwargs)
            bound_args.apply_defaults() # Apply defaults for missing args
            
            return tool(**bound_args.arguments)
        except TypeError as e:
            return f"ERROR: Argumentos inválidos para la herramienta '{tool_name}': {e}. Argumentos recibidos: {kwargs}"
        except Exception as e:
            logger.error(f"Error al ejecutar la herramienta '{tool_name}': {e}")
            return f"ERROR: Fallo en la ejecución de la herramienta '{tool_name}': {e}"

    # --- Tool Definitions ---
    def get_market_data(self, symbol: str, timeframe: str = "1m") -> dict:
        """
        Obtiene los datos de la última vela del mercado para un símbolo y timeframe dados.
        Retorna: {'open', 'high', 'low', 'close', 'volume', 'open_time'}
        """
        try:
            klines = self.binance_client.get_historical_klines(symbol, timeframe, limit=1).get("prices", [])
            if klines:
                latest_candle = klines[-1]
                return {
                    "open": latest_candle.get("open"),
                    "high": latest_candle.get("high"),
                    "low": latest_candle.get("low"),
                    "close": latest_candle.get("close"),
                    "volume": latest_candle.get("volume"),
                    "open_time": latest_candle.get("open_time"),
                }
            return {"error": "No se pudieron obtener datos de mercado."}
        except Exception as e:
            logger.error(f"Error en get_market_data para {symbol}-{timeframe}: {e}")
            return {"error": str(e)}

    def get_indicators(self, symbol: str, timeframe: str = "1m", indicators_list: list = None) -> dict:
        """
        Calcula y retorna el valor de los indicadores técnicos para un símbolo y timeframe dados.
        indicators_list: Lista de strings con los nombres de los indicadores a calcular (ej: ['RSI', 'MACD']).
        """
        if indicators_list is None:
            indicators_list = []

        try:
            # Necesitamos suficientes klines para calcular los indicadores
            # Ajustar el límite según los indicadores solicitados
            limit = 200 # Un límite razonable para la mayoría de los indicadores
            klines = self.binance_client.get_historical_klines(symbol, timeframe, limit=limit).get("prices", [])
            df = normalize_klines(klines, min_length=limit - 10)
            if df.empty:
                return {"error": "Datos insuficientes para calcular indicadores."}

            result = {}
            if 'RSI' in indicators_list:
                df = add_rsi(df, window=14) # Default RSI window
                result['RSI'] = df['RSI'].iloc[-1]
            if 'MACD' in indicators_list:
                macd = ta.trend.MACD(df['close'])
                result['MACD'] = macd.macd().iloc[-1]
                result['MACD_Signal'] = macd.macd_signal().iloc[-1]
                result['MACD_Diff'] = macd.macd_diff().iloc[-1]
            if 'EMA_20' in indicators_list:
                df = add_ema(df, window=20)
                result['EMA_20'] = df['EMA_20'].iloc[-1]
            # Add more indicators as needed
            
            return result
        except Exception as e:
            logger.error(f"Error en get_indicators para {symbol}-{timeframe}-{indicators_list}: {e}")
            return {"error": str(e)}

    def place_order(self, symbol: str, direction: str, size: float, stop_loss: float = None, take_profit: float = None) -> dict:
        """
        Ejecuta una orden de mercado.
        direction: 'BUY' o 'SELL'.
        size: Tamaño de la orden.
        stop_loss: Nivel de Stop Loss (opcional).
        take_profit: Nivel de Take Profit (opcional).
        """
        try:
            # El bot ya maneja el epic de BTC/USD, asumimos que es el mismo para el LLM
            epic = self.capital_client.btc_epic # Assuming btc_epic is always BTC/USD
            if not epic:
                return {"error": "EPIC del instrumento no configurado."}

            response = self.capital_client.place_market_order(epic, direction.upper(), size, stop_loss, take_profit)
            if "dealReference" in response:
                return {"success": True, "dealReference": response["dealReference"], "message": "Orden ejecutada."}
            return {"success": False, "message": f"Fallo al ejecutar la orden: {response}"}
        except Exception as e:
            logger.error(f"Error en place_order para {symbol}-{direction}-{size}: {e}")
            return {"success": False, "message": str(e)}

    def get_portfolio(self) -> dict:
        """
        Obtiene el estado actual del portafolio (posiciones abiertas y liquidez).
        """
        try:
            positions = self.capital_client.get_open_positions().get("positions", [])
            accounts = self.capital_client.get_accounts().get("accounts", [])
            
            portfolio_summary = {
                "open_positions": [],
                "available_funds": 0.0,
                "total_equity": 0.0,
            }

            for acc in accounts:
                if acc.get("accountId") == self.capital_client.account_id:
                    portfolio_summary["available_funds"] = acc.get("availableToDeal", 0.0)
                    portfolio_summary["total_equity"] = acc.get("equity", 0.0)
                    break
            
            for pos in positions:
                position_details = pos.get("position", {})
                portfolio_summary["open_positions"].append({
                    "dealId": position_details.get("dealId"),
                    "direction": position_details.get("direction"),
                    "size": position_details.get("size"),
                    "level": position_details.get("level"),
                    "profit_loss": position_details.get("upl"),
                    "epic": pos.get("market", {}).get("epic"),
                })
            return portfolio_summary
        except Exception as e:
            logger.error(f"Error en get_portfolio: {e}")
            return {"error": str(e)}

    def risk_check(self, order_details: dict) -> dict:
        """
        Realiza una verificación de riesgo para una orden propuesta.
        order_details: Diccionario con detalles de la orden (ej: {'symbol': 'BTCUSDT', 'direction': 'BUY', 'size': 0.01}).
        """
        # Placeholder for actual risk management logic
        # In a real scenario, this would check against user-defined risk parameters
        # e.g., max exposure, max loss per trade, etc.
        logger.info(f"Realizando verificación de riesgo para: {order_details}")
        
        # For now, always allow
        return {"passed": True, "message": "Verificación de riesgo aprobada."}

    def run_agent(self, user_query: str) -> str:
        """
        Orchestrates the interaction between the LLM and the tools to fulfill the user's query.
        """
        # Initial prompt to the LLM, including available tools
        tool_descriptions = "\n".join([
            f"- {name}: {tool.__doc__.strip()}" for name, tool in self.tools.items()
        ])
        
        initial_prompt = f"""
        Eres un agente de trading experto. Tu objetivo es ayudar al usuario a tomar decisiones de trading
        ejecutando herramientas y razonando sobre los resultados.

        Herramientas disponibles:
        {tool_descriptions}

        Instrucciones:
        1. Analiza la consulta del usuario.
        2. Decide qué herramienta(s) necesitas usar y con qué argumentos.
        3. Si necesitas información adicional, pídesela al usuario.
        4. Si la consulta implica una acción de trading (comprar/vender), primero usa 'risk_check'.
        5. Responde siempre en español.

        Consulta del usuario: {user_query}
        """
        
        llm_response = self._call_llm(initial_prompt)
        
        # This is a simplified loop. A real agent would iterate, parse tool calls, execute, and feed back.
        # For this initial implementation, we'll assume the LLM directly gives a final answer or a tool call.
        
        # Attempt to parse a tool call from the LLM's response
        # Expected format: TOOL_CALL: tool_name(arg1=value1, arg2=value2)
        if llm_response.startswith("TOOL_CALL:"):
            try:
                tool_call_str = llm_response.replace("TOOL_CALL:", "").strip()
                # Basic parsing, might need more robust regex for complex calls
                match = re.match(r"(\w+)\\((.*)\\)", tool_call_str)
                if match:
                    tool_name = match.group(1)
                    args_str = match.group(2)
                    kwargs = {}
                    # Handle empty args_str for tools with no arguments
                    if args_str:
                        for arg in args_str.split(','):
                            if '=' in arg:
                                key, value = arg.split('=', 1)
                                # Attempt to parse value as JSON, otherwise keep as string
                                try:
                                    kwargs[key.strip()] = json.loads(value.strip())
                                except json.JSONDecodeError:
                                    kwargs[key.strip()] = value.strip().strip("'") # Remove quotes if it's a string
                            else:
                                # Handle positional arguments if necessary, though kwargs are preferred
                                pass
                    
                    tool_result = self._execute_tool(tool_name, **kwargs)
                    return f"Resultado de la herramienta '{tool_name}': {tool_result}"
                else:
                    return f"ERROR: No se pudo parsear la llamada a la herramienta: {tool_call_str}"
            except Exception as e:
                return f"ERROR: Fallo al procesar la llamada a la herramienta del LLM: {e}. Respuesta del LLM: {llm_response}"
        
        return llm_response # If LLM doesn't call a tool, return its direct response
