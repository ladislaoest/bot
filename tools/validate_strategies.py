import pkgutil, importlib, inspect, traceback, sys, os
sys.path.insert(0, os.path.abspath("."))

from strategies.base import BaseStrategy
from strategies.utils import normalize_strategy_result

def find_strategy_classes(package_path="strategies"):
    for finder, name, _ in pkgutil.iter_modules([package_path]):
        modname = f"{package_path}.{name}"
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            print(f"[IMPORT ERROR] {modname}: {e}")
            traceback.print_exc()
            continue
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if inspect.isclass(obj) and issubclass(obj, BaseStrategy) and obj is not BaseStrategy:
                # heurística: si tiene método run o safe_run
                if hasattr(obj, "run") or hasattr(obj, "safe_run"):
                    yield modname, obj

if __name__ == "__main__":
    print("Iniciando validación de estrategias...")
    for modname, cls in find_strategy_classes():
        print(f"=== Validando {modname}.{cls.__name__}")
        try:
            # probar instanciación flexible
            try:
                inst = cls(config={}, aggressiveness_level=5) # Pasar config y aggressiveness_level
            except Exception:
                inst = cls() # Fallback si no acepta argumentos
            
            # Usar safe_run si está disponible, de lo contrario run
            # Crear un mock para binance_data_provider
            class MockBinanceDataProvider:
                def get_historical_klines(self, symbol, interval, limit):
                    # Devolver datos de ejemplo para evitar errores NoneType
                    return {'prices': [{'open_time': 0, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.5, 'volume': 1000.0}]}
            
            mock_binance_data_provider = MockBinanceDataProvider()

            if hasattr(inst, "safe_run"):
                out = inst.safe_run(None, mock_binance_data_provider) # Pasar mock
            else:
                out = inst.run(None, mock_binance_data_provider) # Pasar mock
            
            normalized_out = normalize_strategy_result(out)

            print(f"=> Tipo de salida: {type(normalized_out)}, Salida normalizada: {normalized_out}")
            if not isinstance(normalized_out, dict) or "signal" not in normalized_out or "message" not in normalized_out:
                print("!! INVALIDA: falta signal/message o no es dict")
            elif normalized_out["signal"] == "ERROR":
                print(f"!! ERROR EN ESTRATEGIA: {normalized_out.get('message')}")
            else:
                print("OK")
        except Exception as e:
            print(f"EXCEPTION al ejecutar {cls.__name__}: {e}")
            traceback.print_exc()
    print("Validación de estrategias finalizada.")
