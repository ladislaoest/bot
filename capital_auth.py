import os
from dotenv import load_dotenv
import requests
import json

# Carga las variables del archivo .env
load_dotenv()

# Obtén las credenciales de las variables de entorno
capital_base_url = os.getenv("CAPITAL_BASE_URL")
capital_api_key = os.getenv("CAPITAL_API_KEY")
capital_api_password = os.getenv("CAPITAL_API_PASSWORD") # Usamos CAPITAL_API_PASSWORD
capital_identifier = os.getenv("CAPITAL_IDENTIFIER")

# --- CONFIGURACIÓN DE LA API DE CAPITAL.COM ---
BASE_URL = capital_base_url.replace("/api/v1", "") if capital_base_url else "https://api-capital.backend-capital.com"
LOGIN_ENDPOINT = f"{BASE_URL}/api/v1/session"

# Asegúrate de que las credenciales existan
if not capital_api_key or not capital_identifier or not capital_api_password or not capital_base_url:
    print("Error: Asegúrate de que las variables CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_API_PASSWORD y CAPITAL_BASE_URL")
    print("estén definidas en tu archivo .env. Por ejemplo:")
    print("CAPITAL_BASE_URL=https://api-capital.backend-capital.com/api/v1")
    print("CAPITAL_API_KEY=tu_api_key_aqui")
    print("CAPITAL_IDENTIFIER=tu_usuario_o_email_aqui")
    print("CAPITAL_API_PASSWORD=tu_contraseña_aqui")
else:
    print("Credenciales cargadas. Intentando autenticación...")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-CAP-API-KEY": capital_api_key
    }
    payload = {
        "identifier": capital_identifier,
        "password": capital_api_password, # Usamos capital_api_password
        "encryptedPassword": False
    }

    try:
        response = requests.post(LOGIN_ENDPOINT, headers=headers, data=json.dumps(payload))
        response.raise_for_status() # Lanza una excepción para códigos de estado HTTP de error (4xx o 5xx)

        cst_token = response.headers.get("CST")
        x_security_token = response.headers.get("X-SECURITY-TOKEN")

        if cst_token and x_security_token:
            print("Autenticación exitosa!")
            print(f"CST Token: {cst_token}")
            print(f"X-SECURITY-TOKEN: {x_security_token}")

            # Aquí puedes guardar estos tokens para usarlos en futuras solicitudes
            # Por ejemplo, podrías pasarlos a una clase de cliente de API o guardarlos en una variable global
            # o incluso en variables de entorno temporales si el bot va a seguir ejecutándose.
            # Por ahora, solo los imprimimos.

        else:
            print("Autenticación fallida: No se encontraron los tokens CST o X-SECURITY-TOKEN en los headers de la respuesta.")
            print(f"Headers de la respuesta: {response.headers}")
            print(f"Cuerpo de la respuesta (si existe): {response.text}")

    except requests.exceptions.HTTPError as err:
        print(f"Error HTTP durante la autenticación: {err}")
        print(f"Código de estado: {err.response.status_code}")
        print(f"Respuesta del servidor: {err.response.text}")
    except requests.exceptions.ConnectionError as err:
        print(f"Error de conexión: {err}")
    except requests.exceptions.Timeout as err:
        print(f"Tiempo de espera agotado: {err}")
    except requests.exceptions.RequestException as err:
        print(f"Error inesperado: {err}")
    except json.JSONDecodeError:
        print("Error al decodificar la respuesta JSON del servidor (la respuesta no fue JSON).")
        print(f"Respuesta completa: {response.text}")
