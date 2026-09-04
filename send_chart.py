import os
import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TV_SESSIONID = os.getenv("TRADINGVIEW_SESSIONID")

CHART_LAYOUT_URL = "https://it.tradingview.com/chart/KyeIeMP6/?symbol=XETR%3ABMW"

def take_chart_screenshot(output_path="bmw_chart.png"):
    print("Avvio browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1100},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Se presente, inietta il cookie di sessione per autenticare l'account
        if TV_SESSIONID:
            context.add_cookies([
                {
                    "name": "sessionid",
                    "value": TV_SESSIONID,
                    "domain": ".tradingview.com",
                    "path": "/"
                }
            ])
            print("Cookie di sessione TradingView caricato.")

        page = context.new_page()
        page.goto(CHART_LAYOUT_URL, wait_until="domcontentloaded")

        # Attende 10 secondi per caricare la serie storica e calcolare gli indicatori
        page.wait_for_timeout(10000)

        # Chiude la barra laterale della Watchlist premendo Alt+W se aperta
        try:
            page.keyboard.press("Alt+w")
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Chiude eventuali modali o banner premendo Escape
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

        # Scatta lo screenshot
        page.screenshot(path=output_path)
        browser.close()
    print("Screenshot catturato con indicatori caricati.")

def send_telegram_photo(image_path="bmw_chart.png"):
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN mancante o vuoto.")
    if not CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID mancante o vuoto.")

    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    caption = "BMW (XETR:BMW)"

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
