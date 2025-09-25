import pandas as pd

def normalize_klines(klines):
    if not klines:
        return pd.DataFrame()

    first_kline_len = len(klines[0])
    
    if first_kline_len == 5:
        # Klines con 5 elementos: [open_time, open, high, low, close]
        # Asumimos que el primer elemento es open_time, y no hay volumen
        df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close'])
        df['volume'] = 0.0  # Rellenar volumen con 0.0
    elif first_kline_len == 12:
        # Klines con 12 elementos: [open_time, open, high, low, close, volume, ...]
        df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume',
                                           'close_time', 'quote_asset_volume', 'number_of_trades',
                                           'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']] # Seleccionar solo las columnas necesarias
    else:
        raise ValueError(f"Formato de klines no soportado: {first_kline_len} elementos por kline.")

    # Convertir columnas numéricas
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Convertir open_time a datetime
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce')
    
    return df