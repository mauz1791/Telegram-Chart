import os
import time
import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TV_SESSIONID = os.getenv("TRADINGVIEW_SESSIONID")

# Il tuo layout con indicatori
BASE_LAYOUT_URL = "https://it.tradingview.com/chart/KyeIeMP6/"

# LISTA DELLE AZIONI PER IL REPORT
# Aggiungi qui tutte le azioni che vuoi includere
STOCKS = [
    {
        "name": "ABBN",
        "symbol": "SIX:ABBN",
        "timeframe": "1W",
    },
    {
        "name": "ACCOR SA",
        "symbol": "EURONEXT:AC",
        "timeframe": "1W",
    },
    {
        "name": "ADECCO GROUP AG",
        "symbol": "SIX:ADEN",
        "timeframe": "1W",
    },
    {
        "name": "ADIDAS AG",
        "symbol": "XETR:ADS",
        "timeframe": "1W",
    },
    {
        "name": "CARL ZEISS MEDITEC AG",
        "symbol": "XETR:AFX",
        "timeframe": "1W",
    },
    {
        "name": "AHOLD DELHAIZE N.V.",
        "symbol": "EURONEXT:AD",
        "timeframe": "1W",
    },
    {
        "name": "AIR LIQUIDE",
        "symbol": "EURONEXT:AI",
        "timeframe": "1W",
    },    
    {
        "name": "AIXTRON",
        "symbol": "XETR:AIXA",
        "timeframe": "1W",
    },
    {
        "name": "AKZO NOBEL N.V.",
        "symbol": "EURONEXT:AKZA",
        "timeframe": "1W",
    },
    {
        "name": "ALCON AG",
        "symbol": "SIX:ALC",
        "timeframe": "1W",
    },
    {
        "name": "ALLIANZ SE",
        "symbol": "XETR:ALV",
        "timeframe": "1W",
    },
    {
        "name": "AUMOVIO SE",
        "symbol": "XETR:AMV0",
        "timeframe": "1W",
    },
    {
        "name": "VONOVIA SE",
        "symbol": "XETR:VNA",
        "timeframe": "1W",
    },
    {
        "name": "ARYZTA AG",
        "symbol": "SIX:ARYN",
        "timeframe": "1W",
    },
    {
        "name": "ASSICURAZIONI GENERALI SPA",
        "symbol": "MIL:G",
        "timeframe": "1W",
    },
    {
        "name": "ASML HOLDING N.V.",
        "symbol": "EURONEXT:ASML",
        "timeframe": "1W",
    },
    {
        "name": "AROUNDTOWN SA",
        "symbol": "XETR:AT1",
        "timeframe": "1W",
    },
    {
        "name": "AXA SA",
        "symbol": "EURONEXT:CS",
    },
    {
        "name": "JULIUS BAER GRUPPE",
        "symbol": "SIX:BAER",
    },
    {
        "name": "HELVETIA BALOISE HOLDING AD",
        "symbol": "SIX:HBAN",
    },
    {
        "name": "BASF SE",
        "symbol": "XETR:BAS",
    },
    {
        "name": "BAYER AG",
        "symbol": "XETR:BAYN",
    },
    {
        "name": "BANCO BILBAO VIZCAYA",
        "symbol": "BME:BBVA",
    },
    {
        "name": "BECHTLE AG",
        "symbol": "XETR:BC8",
    },
    {
        "name": "BEIERSDORF AG",
        "symbol": "XETR:BEI",
    },
    {
        "name": "BMW",
        "symbol": "XETR:BMW",
    },
    {
        "name": "BNP PARIBAS",
        "symbol": "EURONEXT:BNP",
    },
    {
        "name": "BRENNTAG SE",
        "symbol": "XETR:BNR",
    },
    {
        "name": "HUGO BOSS AG",
        "symbol": "XETR:BOSS",
    },
    {
        "name": "BANCO SANTANDER S.A.",
        "symbol": "BME:SAN",
    },
    {
        "name": "DANONE SE",
        "symbol": "EURONEXT:BN",
    },
    {
        "name": "BOUYGUES SA",
        "symbol": "EURONEXT:EN",
    },
    {
        "name": "RWE",
        "symbol": "XETR:RWE",
        "timeframe": "1W",
    },
    {
        "name": "DTE",
        "symbol": "XETR:DTE",
        "timeframe": "1W",
    },
    {
        "name": "MBG",
        "symbol": "XETR:MBG",
        "timeframe": "1W",
    },
    {
        "name": "CBK",
        "symbol": "XETR:CBK",
        "timeframe": "1W",
    },
    # Esempio per aggiungerne un'altra:
    # {
    #     "name": "Intesa Sanpaolo",
    #     "symbol": "MIL:ISP",
    #     "timeframe": "1W",
    # },
]

def capture_all_charts():
    generated_files = []
    print(f"Avvio cattura per {len(STOCKS)} strumenti...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1100},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Iniezione cookie per caricare gli indicatori personali
        if TV_SESSIONID:
            context.add_cookies([
                {
                    "name": "sessionid",
                    "value": TV_SESSIONID,
                    "domain": ".tradingview.com",
                    "path": "/"
                }
            ])

        page = context.new_page()

        for stock in STOCKS:
            name = stock["name"]
            symbol = stock["symbol"]
            file_path = f"chart_{name.replace(' ', '_')}.png"

            # Costruisce l'URL con il simbolo desiderato
            target_url = f"{BASE_LAYOUT_URL}?symbol={symbol.replace(':', '%3A')}"
            print(f"Caricamento {name} ({symbol})...")

            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(9000)  # Attende il ricalcolo di MACD

            # Chiude eventuali pannelli laterali o banner
            try:
                page.keyboard.press("Alt+w")
                page.wait_for_timeout(500)
            except Exception:
                pass
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            page.screenshot(path=file_path)
            print(f"Screenshot salvato: {file_path}")

            generated_files.append((file_path, stock))

        browser.close()
    return generated_files

def send_to_telegram(charts):
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("Credenziali Telegram mancanti nei Secrets.")

    print("Invio reportage su Telegram...")
    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    # Invia un messaggio introduttivo per il report
    intro_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(intro_url, data={
        "chat_id": CHAT_ID,
        "text": f"📈 *REPORT SETTIMANALE GRAFICI*\nStrumenti analizzati: {len(charts)}",
        "parse_mode": "Markdown"
    })

    # Invia ciascun grafico con la sua etichetta
    for file_path, stock in charts:
        caption = f"📊 *{stock['name']}* ({stock['symbol']}) — Timeframe: {stock.get('timeframe', '1W')}"
        with open(file_path, "rb") as img:
            response = requests.post(
                telegram_url,
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": img}
            )
        if response.status_code == 200:
            print(f"Inviato: {stock['name']}")
        else:
            print(f"Errore invio {stock['name']}: {response.text}")
        
        # Breve pausa per evitare limiti di frequenza di Telegram
        time.sleep(1.5)

if __name__ == "__main__":
    charts = capture_all_charts()
    send_to_telegram(charts)
