import os
from dotenv import load_dotenv

load_dotenv(override=True)

api_key = os.getenv("CAPITAL_API_KEY", "")

print("=== DEBUG API KEY ===")
print(f"API Key (entre corchetes): [{api_key}]")
print(f"Longitud: {len(api_key)}")
print("Caracteres con ASCII:")
for i, c in enumerate(api_key):
    print(f"{i}: {c} (ord={ord(c)})")