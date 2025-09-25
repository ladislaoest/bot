from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

def normalize_strategy_result(out: Any) -> Dict:
    """
    Asegura que la salida es un dict con keys mínimas para el dashboard.
    """
    if not isinstance(out, dict):
        logger.warning(f"Resultado inválido: tipo inesperado {type(out).__name__}. Se esperaba un diccionario.")
        return {
            "signal": "ERROR",
            "message": f"Invalid return type: {type(out).__name__}",
            "detailed_status": {"raw": str(out)},
            "entry": None,
            "sl_pct": None,
            "tp_pct": None
        }
    # Campos obligatorios con fallback
    res = {
        "signal": out.get("signal", "HOLD"),
        "message": out.get("message") or f"HOLD: sin mensaje proporcionado ({out.get('signal','HOLD')})",
        "detailed_status": out.get("detailed_status", {}) or {},
        "entry": out.get("entry", None),
        "sl_pct": out.get("sl_pct", None),
        "tp_pct": out.get("tp_pct", None)
    }
    # Copiar otros campos útiles (opcionales)
    for k in ("extra",): # Puedes añadir más claves si son comunes y útiles
        if k in out:
            res[k] = out[k]
    return res
