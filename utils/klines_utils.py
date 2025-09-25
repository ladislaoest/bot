import pandas as pd

def normalize_klines(prices, min_length=0):
    """
    Convierte cualquier formato de klines (list[dict], list[list], etc.)
    en un DataFrame uniforme con columnas estándar.
    """

    if not prices:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

    first = prices[0]

    # Diccionario
    if isinstance(first, dict):
        df = pd.DataFrame(prices)
        if "open_time" not in df.columns:
            df["open_time"] = pd.NA
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df = df[["open_time", "open", "high", "low", "close", "volume"]]

    # Lista
    elif isinstance(first, (list, tuple)):
        if len(first) == 5:
            cols = ["open_time", "open", "high", "low", "close"]
        elif len(first) == 12:
            cols = [
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
            ]
        else:
            raise ValueError(f"Formato de kline desconocido: {len(first)} elementos")

        df = pd.DataFrame(prices, columns=cols)
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df = df[["open_time", "open", "high", "low", "close", "volume"]]

    else:
        raise TypeError("Formato de kline no soportado")

    # Validación mínima de longitud
    if len(df) < min_length:
        return pd.DataFrame(columns=df.columns)

    return df