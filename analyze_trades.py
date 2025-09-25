import csv
import pandas as pd

def analyze_trades(file_path="trade_history.csv"):
    """
    Lee el historial de operaciones desde un archivo CSV, calcula el rendimiento
    general y por estrategia, y muestra un resumen.
    """
    try:
        # Usar pandas para leer el CSV y manejar posibles problemas
        df = pd.read_csv(file_path, on_bad_lines='skip')
        # Asegurarse que la columna profit_loss sea numérica
        df['profit_loss'] = pd.to_numeric(df['profit_loss'], errors='coerce')
        # Filtrar solo las operaciones cerradas y con datos de P/L válidos
        closed_trades = df[df['status'] == 'CLOSED'].dropna(subset=['profit_loss'])

        if closed_trades.empty:
            print("No hay operaciones cerradas para analizar en el historial.")
            return

        # --- Resumen General ---
        total_pnl = closed_trades['profit_loss'].sum()
        total_trades = len(closed_trades)
        winning_trades = closed_trades[closed_trades['profit_loss'] > 0]
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0

        print("--- Resumen de Rendimiento General ---")
        print(f"Ganancia/Pérdida Total: {total_pnl:.2f}")
        print(f"Número Total de Operaciones Cerradas: {total_trades}")
        print(f"Tasa de Acierto General: {win_rate:.2f}%")
        print("\n" + "="*40 + "\n")

        # --- Resumen por Estrategia ---
        strategy_performance = closed_trades.groupby('strategy').agg(
            total_profit_loss=('profit_loss', 'sum'),
            total_trades=('profit_loss', 'count'),
            winning_trades=('profit_loss', lambda x: (x > 0).sum())
        ).reset_index()

        strategy_performance['win_rate'] = (strategy_performance['winning_trades'] / strategy_performance['total_trades'] * 100)

        print("--- Resumen de Rendimiento por Estrategia ---")
        for _, row in strategy_performance.iterrows():
            print(f"Estrategia: {row['strategy']}")
            print(f"  Ganancia/Pérdida Total: {row['total_profit_loss']:.2f}")
            print(f"  Número Total de Operaciones: {row['total_trades']}")
            print(f"  Tasa de Acierto: {row['win_rate']:.2f}%")
            print("-" * 30)

    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{file_path}'.")
        print("Asegúrate de que el bot se haya ejecutado y generado el historial de operaciones.")
    except Exception as e:
        print(f"Ocurrió un error inesperado al analizar los trades: {e}")

if __name__ == "__main__":
    analyze_trades()