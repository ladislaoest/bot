import os
import json
import time
from dotenv import load_dotenv
from capital_bot import TradingBot, CapitalComAPIClient, BinanceAPIClient # Importar TradingBot

# Cargar variables de entorno para las credenciales de la API
load_dotenv()

# Cargar configuración global
with open('config.json', 'r') as f:
    config = json.load(f)

# --- Inicialización del Bot y Clientes de API ---
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
    raise ValueError(f"No se pudo encontrar la cuenta '{target_account_name}'. Cuentas disponibles: {[acc.get('accountName') for acc in accounts.get('accounts', [])]}. ")

# Crear la instancia final de CapitalComAPIClient con el account_id correcto
capital_client = CapitalComAPIClient(account_id=selected_account_id)

binance_client = BinanceAPIClient(api_key=os.getenv("BINANCE_API_KEY"), api_secret=os.getenv("BINANCE_API_SECRET"))

# Instanciar el TradingBot (esto iniciará el polling automáticamente)
print("DEBUG: Instanciando TradingBot...") # DEBUG PRINT
trading_bot = TradingBot(capital_client_api=capital_client, binance_client_api=binance_client)

# Mantener el script en ejecución para que el bot siga operando
try:
    while True:
        time.sleep(1) # Pequeña pausa para no consumir CPU innecesariamente
except KeyboardInterrupt:
    print("Bot detenido manualmente.")
    trading_bot.stop_polling()