def base_strategy_extended(prices, tf="1m", name="GenericStrategy", direction="both", min_confluence_score=3):
    """
    Plantilla extendida para estrategias con sistema de puntuación de confluencia.
    - Normaliza klines
    - Calcula EMA, RSI, MACD, ATR
    - Genera señales basadas en un score de condiciones
    - Maneja lógicas long, short y both
    """

    try:
        df = normalize_klines(prices, min_length=50)
        if df.empty:
            print(f"DEBUG {name}: Datos insuficientes en {tf} (DataFrame vacío después de normalizar).")
            return f"HOLD - {name}: Datos insuficientes en {tf}"

        # --- Indicadores ---
        df = add_ema(df, 9)
        df = add_ema(df, 20)
        df = add_ema(df, 50)
        df = add_rsi(df, 14)
        
        if len(df) >= 26:
            macd = ta.trend.MACD(df['close'])
            df['MACD'] = macd.macd()
            df['MACD_Signal'] = macd.macd_signal()
            df['MACD_Diff'] = macd.macd_diff()
        else:
            df['MACD'] = df['MACD_Signal'] = df['MACD_Diff'] = pd.NA

        if len(df) >= 14:
            df['ATR'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        else:
            df['ATR'] = pd.NA

        latest = df.iloc[-1]

        # --- Lógica de Puntuación de Confluencia (BUY) ---
        buy_conditions = []
        # Condición 1: Precio por encima de la EMA media (tendencia)
        cond1_buy = latest["close"] > latest["EMA20"]
        buy_conditions.append(cond1_buy)
        # Condición 2: RSI fuerte (momentum)
        cond2_buy = latest["RSI"] > 55
        buy_conditions.append(cond2_buy)
        # Condición 3: MACD cruzando hacia arriba (confirmación de momentum)
        cond3_buy = latest["MACD"] > latest["MACD_Signal"]
        buy_conditions.append(cond3_buy)
        # Condición 4 (NUEVA): EMA rápida por encima de la media (momentum a corto plazo)
        cond4_buy = latest["EMA9"] > latest["EMA20"]
        buy_conditions.append(cond4_buy)
        
        buy_score = sum(buy_conditions)
        print(f"DEBUG {name} - BUY: C1={cond1_buy}, C2={cond2_buy}, C3={cond3_buy}, C4={cond4_buy} -> Score={buy_score}/4. Min Score: {min_confluence_score}")

        # --- Lógica de Puntuación de Confluencia (SELL) ---
        sell_conditions = []
        # Condición 1: Precio por debajo de la EMA media (tendencia)
        cond1_sell = latest["close"] < latest["EMA20"]
        sell_conditions.append(cond1_sell)
        # Condición 2: RSI débil (momentum)
        cond2_sell = latest["RSI"] < 45
        sell_conditions.append(cond2_sell)
        # Condición 3: MACD cruzando hacia abajo (confirmación de momentum)
        cond3_sell = latest["MACD"] < latest["MACD_Signal"]
        sell_conditions.append(cond3_sell)
        # Condición 4 (NUEVA): EMA rápida por debajo de la media (momentum a corto plazo)
        cond4_sell = latest["EMA9"] < latest["EMA20"]
        sell_conditions.append(cond4_sell)

        sell_score = sum(sell_conditions)
        print(f"DEBUG {name} - SELL: C1={cond1_sell}, C2={cond2_sell}, C3={cond3_sell}, C4={cond4_sell} -> Score={sell_score}/4. Min Score: {min_confluence_score}")

        # --- Generación de Señales ---
        signal = f"HOLD - {name}: Buy Score {buy_score}/4, Sell Score {sell_score}/4"

        if direction in ["long", "both"] and buy_score >= min_confluence_score:
            signal = f"BUY - {name}: Score {buy_score}/4 en {tf}"
        
        elif direction in ["short", "both"] and sell_score >= min_confluence_score:
            signal = f"SELL - {name}: Score {sell_score}/4 en {tf}"

        return signal

    except Exception as e:
        return f"ERROR - {name}: {str(e)}"
