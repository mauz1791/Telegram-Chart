import os
import requests
from playwright.sync_api import sync_playwright

# Recupera i parametri dalle variabili d'ambiente
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# Ticker per BMW su XETRA (puoi cambiarlo ad esempio con MIL:BMW)
SYMBOL = os.getenv("SYMBOL", "XETR:BMW")

def take_chart_screenshot(output_path="bmw_chart.png"):
    # URL del widget TradingView ottimizzato a tutto schermo
    url = (
        f"https://s.tradingview.com/widgetembed/?"
        f"symbol={SYMBOL}&interval=D&theme=dark&style=1"
        f"&timezone=Europe%2FRome&hide_side_toolbar=1&withdateranges=1"
    )

    print(f"Caricamento grafico per {SYMBOL}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Risoluzione dello screenshot (16:9)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        
        # Carica la pagina e attende che il canvas di TradingView si stabilizzi
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)  # Attende 6 secondi per il render completo dei dati
        
        page.screenshot(path=output_path)
        browser.close()
    print("Screenshot catturato con successo.")

def send_telegram_photo(image_path="bmw_chart.png"):
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("Variabili TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti.")

    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    caption = f"📊 Grafico giornaliero BMW ({SYMBOL}) - TradingView"

    with open(image_path, "rb") as img:
        response = requests.post(
            telegram_url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": img}
        )

    if response.status_code == 200:
        print("Grafico inviato correttamente su Telegram!")
    else:
        print(f"Errore durante l'invio su Telegram: {response.text}")
        response.raise_for_status()

if __name__ == "__main__":
    take_chart_screenshot()
    send_telegram_photo()
