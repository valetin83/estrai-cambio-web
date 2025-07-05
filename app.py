import pandas as pd
from flask import Flask, send_from_directory, render_template
import os

app = Flask(__name__)
CSV_PATH = os.path.join("public", "report.csv")
LOG_PATH = os.path.join("logs", "log.txt")

def load_csv():
    if os.path.exists(CSV_PATH):
        df = pd.read_csv(CSV_PATH, sep=';', decimal=',')
        df['Data'] = pd.to_datetime(df['Data'], dayfirst=True)
        return df.sort_values('Data', ascending=False)
    return pd.DataFrame()

def parse_last_status():
    if not os.path.exists(LOG_PATH):
        return "⚠️ Nessuna esecuzione registrata"
    with open(LOG_PATH, encoding='utf-8') as log:
        lines = [line for line in log if line.strip()]
        for line in reversed(lines):
            if "Esecuzione avviata" in line or "ESITO:" in line or "Esecuzione saltata" in line:
                return line.strip()
    return "📄 Log presente, ma nessuna esecuzione recente trovata"

@app.route('/')
def home():
    df = load_csv()
    status = parse_last_status()
    return render_template('index.html', dati=df.to_dict(orient='records'), stato=status)

@app.route('/download')
def download_csv():
    return send_from_directory('public', 'report.csv', as_attachment=True)

@app.route('/log')
def download_log():
    return send_from_directory('logs', 'log.txt', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)