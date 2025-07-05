#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import configparser
import csv
import importlib.util
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import sys
import shutil
from pathlib import Path
from datetime import datetime, time as dt_time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ─── Auto‐install delle dipendenze ─────────────────────────────
def install_if_missing(package: str, import_name: str = None) -> None:
    name = import_name or package
    if importlib.util.find_spec(name) is None:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        except subprocess.CalledProcessError as e:
            print(f"❌ Errore installazione '{package}': {e}")
            sys.exit(1)

for pkg, imp in [("requests", None), ("beautifulsoup4", "bs4"), ("lxml", None)]:
    install_if_missing(pkg, imp)

# ─── CLI & Config ─────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Estrai cambio EUR da OTP banka")
parser.add_argument("--config", default="config.ini", help="Percorso file INI")
args = parser.parse_args()

cfg = configparser.ConfigParser()
cfg.read(args.config, encoding="utf-8")
conf = cfg["DEFAULT"]

def get_opt(key: str, fallback: str = None) -> str:
    return conf.get(key, fallback)

START     = get_opt("start")
END       = get_opt("end")
WEEKDAYS  = set(int(x) for x in get_opt("weekdays", "0,1,2,3,4").split(","))
URL       = get_opt("url")
CSV_PATH  = Path(get_opt("csv_path", "report.csv"))
LOG_PATH  = Path(get_opt("log_path", "log.txt"))
CURRENCY  = get_opt("currency", "EUR")
SKIP_IF_EXISTS = conf.getboolean("skip_if_exists", fallback=True)

# ─── Backup log se supera MAX ──────────────────────────────────
LOG_MAX_BYTES = 200 * 1024
if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_dir = LOG_PATH.parent / "logs"
    backup_dir.mkdir(exist_ok=True)
    backup_file = backup_dir / f"log-{timestamp}.log"
    shutil.copy(LOG_PATH, backup_file)
    LOG_PATH.unlink()

# ─── Logger UTF-8 rotante ─────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("estrai_eur")
logger.setLevel(logging.INFO)
fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
fh = RotatingFileHandler(LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=0, encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Controlli orario e giorno ───────────────────────────────
def check_time_window(start: str, end: str) -> None:
    now = datetime.now().time()
    if not (dt_time.fromisoformat(start) <= now <= dt_time.fromisoformat(end)):
        raise RuntimeError(f"Esecuzione consentita tra {start} e {end}. Ora: {now.strftime('%H:%M')}")

def check_weekday(valid: set[int]) -> None:
    today = datetime.now().weekday()
    if today not in valid:
        raise RuntimeError(f"Esecuzione valida solo nei giorni {sorted(valid)}. Oggi: {today}")

# ─── HTTP session con retry ───────────────────────────────────
def create_session(retries: int = 3, backoff: float = 0.3) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=retries, backoff_factor=backoff, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# ─── Estrazione tassi EUR ─────────────────────────────────────
def extract_eur_rate(session: requests.Session, url: str, currency: str) -> tuple[str, str]:
    logger.info(f"🌐 Invio GET a {url}")
    resp = session.get(url, timeout=10)
    resp.raise_for_status()

    parser = "lxml" if importlib.util.find_spec("lxml") else "html.parser"
    soup = BeautifulSoup(resp.text, parser)

    table = soup.find("table")
    if table is None:
        raise RuntimeError("❌ Tabella non trovata")

    header_row = table.find("thead") or table.find("tr")
    headers = [th.get_text(strip=True) for th in header_row.find_all("th")]

    try:
        idx_cur = headers.index("Currency")
        idx_mid = headers.index("Middle")
        idx_sal = headers.index("Sales")
    except ValueError as ve:
        raise RuntimeError(f"❌ Intestazioni mancanti: {ve}. Trovate: {headers}")

    rows = (table.find("tbody") or table).find_all("tr")
    for tr in rows:
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        if cols[idx_cur].upper() == currency.upper():
            raw_mid = cols[idx_mid]
            raw_sal = cols[idx_sal]
            try:
                float(raw_mid.replace(",", "."))
                float(raw_sal.replace(",", "."))
            except ValueError:
                raise RuntimeError(f"❌ Valori non numerici: middle={raw_mid}, sales={raw_sal}")
            mid = raw_mid.replace(".", ",")
            sal = raw_sal.replace(".", ",")
            logger.info(f"💶 {currency} estratto → Middle: {mid}, Sales: {sal}")
            return mid, sal

    raise RuntimeError(f"❌ Valuta {currency} non trovata nella tabella")

# ─── Scrittura CSV sicura ─────────────────────────────────────
def save_csv(path: Path, middle: str, sales: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%d/%m/%Y")
    header = ["Data", "Middle", "Sales"]
    row = [today, middle, sales]
    temp = path.with_suffix(".tmp")

    existing = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            existing = list(csv.reader(f, delimiter=";"))
    if not existing or existing[0] != header:
        existing.insert(0, header)

    dates = [r[0] for r in existing]
    if today in dates:
        existing[dates.index(today)] = row
    else:
        existing.append(row)

    with temp.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerows(existing)
    temp.replace(path)

    if path.exists():
        logger.info(f"✅ File CSV creato o aggiornato correttamente: {path.resolve()}")
    else:
        logger.error("❌ Il file CSV non è stato trovato dopo la scrittura!")
        raise RuntimeError("Errore nella creazione del file CSV")

# ─── Main aggiornato ───────────────────────────────────────────────
def main() -> None:
    logger.info("🚀 === Avvio script con successo ===")
    check_weekday(WEEKDAYS)
    logger.info("📆 Giorno valido")
    check_time_window(START, END)
    logger.info("⏰ Orario valido")

    today = datetime.now().strftime("%d/%m/%Y")

    if SKIP_IF_EXISTS and CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8") as f:
            rows = list(csv.reader(f, delimiter=";"))
        dates = [r[0] for r in rows[1:]] if len(rows) > 1 else []
        if today in dates:
            logger.info("📌 Dati già estratti per oggi")
            return

    session = create_session()
    mid, sal = extract_eur_rate(session, URL, CURRENCY)
    save_csv(CSV_PATH, mid, sal)

    logger.info("🏁 === Fine script con successo ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ Errore in esecuzione: {e}", exc_info=True)
        sys.exit(1)