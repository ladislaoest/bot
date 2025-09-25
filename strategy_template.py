import pandas as pd
from utils.klines_utils import normalize_klines
from utils.indicators import add_ema, add_rsi

def base_strategy(prices, tf="1m", name="GenericStrategy"):
    """
    Plantilla base para estrategias.
    - Normaliza datos de klines
    - Calcula EMA20, EMA50 y RSI14
    - Devuelve señal en formato estándar
    """

    try:
        # 1. Normalización con longitud mínima (50 velas por defecto)
        df = normalize_klines(prices, min_length=50)
        if df.empty:
            return f"HOLD - {name}: Datos insuficientes en {tf}"

        # 2. Indicadores comunes
        df = add_ema(df, 9)
        df = add_ema(df, 20)
        df = add_ema(df, 50)
        df = add_rsi(df, 14)

        latest = df.iloc[-1]

        # 3. Validación de indicadores
        if pd.isna(latest["EMA9"]) or pd.isna(latest["EMA20"]) or pd.isna(latest["EMA50"]) or pd.isna(latest["RSI"]):
            return f"HOLD - {name}: Indicadores no disponibles todavía"

        # 4. Ejemplo de lógica genérica (puedes cambiarla en cada estrategia)
        if latest["close"] > latest["EMA20"] and latest["RSI"] > 55:
            return f"BUY - {name}: Precio {latest['close']:.2f} sobre EMA20 {latest['EMA20']:.2f}, RSI {latest['RSI']:.2f}"
        elif latest["close"] < latest["EMA20"] and latest["RSI"] < 45:
            return f"SELL - {name}: Precio {latest['close']:.2f} bajo EMA20 {latest['EMA20']:.2f}, RSI {latest['RSI']:.2f}"
        else:
            return f"HOLD - {name}: Precio {latest['close']:.2f}, EMA20 {latest['EMA20']:.2f}, RSI {latest['RSI']:.2f}"

    except Exception as e:
        return f"ERROR - {name}: {str(e)}"