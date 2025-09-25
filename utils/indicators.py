import pandas as pd
import ta

def add_ema(df, period):
    df_copy = df.copy() # Crear una copia explícita
    if df_copy.empty or len(df_copy) < period:
        df_copy[f"EMA{period}"] = pd.Series([pd.NA] * len(df_copy))
    else:
        df_copy[f"EMA{period}"] = ta.trend.EMAIndicator(close=df_copy["close"], window=period).ema_indicator()
    return df_copy

def add_rsi(df, period=14):
    df_copy = df.copy() # Crear una copia explícita
    if df_copy.empty or len(df_copy) < period + 1:
        df_copy["RSI"] = pd.Series([pd.NA] * len(df_copy))
        return df_copy

    rsi_indicator = ta.momentum.RSIIndicator(close=df_copy["close"], window=period)
    df_copy["RSI"] = rsi_indicator.rsi()
    return df_copy

def scale_aggressiveness(base_value, aggressiveness_level, min_factor, max_factor):
    """
    Escala un valor base linealmente en función del nivel de agresividad.
    aggressiveness_level: 1 (menos agresivo) a 10 (más agresivo).
    min_factor: Factor de escala aplicado en el nivel 1.
    max_factor: Factor de escala aplicado en el nivel 10.
    """
    if aggressiveness_level < 1:
        aggressiveness_level = 1
    elif aggressiveness_level > 10:
        aggressiveness_level = 10

    # Normalizar el nivel de agresividad a un rango de 0 a 1
    normalized_level = (aggressiveness_level - 1) / 9 # (10 - 1)

    # Interpolar linealmente entre min_factor y max_factor
    scaled_factor = min_factor + (max_factor - min_factor) * normalized_level

    return base_value * scaled_factor