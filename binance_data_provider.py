import time
import random

def get_historical_klines(symbol, interval, limit=250):
    """
    Dummy function to generate plausible historical kline data.
    Returns data in the format expected by the calling script.
    """
    klines = []
    # interval to milliseconds
    interval_ms = 0
    if 'm' in interval:
        interval_ms = int(interval.replace('m', '')) * 60 * 1000
    elif 'h' in interval:
        interval_ms = int(interval.replace('h', '')) * 60 * 60 * 1000
    
    if interval_ms == 0: # default to 1m if something is wrong
        interval_ms = 60 * 1000

    current_time = int(time.time() * 1000)
    price = 115000.0 # Starting price for BTCUSDT-like symbol

    for i in range(limit):
        open_time = current_time - (limit - i) * interval_ms
        open_price = price
        high_price = open_price * (1 + random.uniform(0, 0.005))
        low_price = open_price * (1 - random.uniform(0, 0.005))
        close_price = random.uniform(low_price, high_price)
        volume = random.uniform(10, 100)
        
        klines.append({
            "open_time": open_time,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume
        })
        price = close_price # next open is this close

    return {"prices": klines}