import os
import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# URL diretto al tuo layout personale
CHART_LAYOUT_URL = "https://it.tradingview.com/chart/KyeIeMP6/?symbol=XETR%3ABMW"

def take_chart_screenshot(output_path="bmw_chart.png"):
    print("Apertura layout TradingView BMW...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Imposta una risoluzione ampia e un User-Agent desktop per evitare versioni mobile o tagli
        context = browser.new_context(
            viewport={"width": 1400, "height": 1050},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(CHART_LAYOUT_URL, wait_until="domcontentloaded")
        
        # Attesa necessaria per caricare i dati storici e calcolare gli script Pine
        page.wait_for_timeout(9000)
        
        # Chiude eventuali overlay, notifiche o menu aperti
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        
        page.screenshot(path=output_path)
        browser.close()
    print("Screenshot con indicatori catturato con successo.")

def send_telegram_photo(image_path="bmw_chart.png"):
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN mancante o vuoto.")
    if not CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID mancante o vuoto.")

    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    caption = "📊 Aggiornamento Grafico BMW (XETR:BMW) - TradingView"

    with open(image_path, "rb") as img:
        response = requests.post(
            telegram_url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": img}
        )

    if response.status_code == 200:
        print("Grafico inviato correttamente su Telegram!")
    else:
        print(f"Errore Telegram: {response.text}")
        response.raise_for_status()

if __name__ == "__main__":
    take_chart_screenshot()
    send_telegram_photo()
