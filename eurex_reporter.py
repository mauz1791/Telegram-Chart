import os
import sys
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def send_telegram_photo(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as img:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML"
        }
        files = {"photo": img}
        resp = requests.post(url, data=payload, files=files, timeout=30)
        
    if resp.ok:
        print("✅ Foto inviata su Telegram!")
    else:
        print(f"❌ Errore Telegram Photo: {resp.text}")
        send_telegram_text(caption)

def send_telegram_text(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload, timeout=15)

def main():
    target_url = "https://it.tradingview.com/chart/KyeIeMP6/?symbol=XETR%3ABMW"
    screenshot_path = "eurex_bmw.png"
    
    print("Avvio Playwright Chromium...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 1300},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print(f"Navigazione verso {target_url}...")
        page.goto(target_url, wait_until="networkidle", timeout=60000)
        
        # Gestione Cookie Banner
        try:
            cookie_btn = page.locator("button:has-text('Accept'), button:has-text('Accetta'), #cookie-accept-all")
            if cookie_btn.count() > 0:
                cookie_btn.first.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # Clic sulla scheda Statistics
        try:
            stats_tab = page.locator("a:has-text('Statistics'), button:has-text('Statistics')")
            if stats_tab.count() > 0:
                stats_tab.first.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass
            
        page.wait_for_timeout(3000)
        page.screenshot(path=screenshot_path, full_page=False)
        browser.close()

    caption = (
        "📊 <b>REPORT EUREX BMW — AGGIORNAMENTO GIORNALIERO</b>\n\n"
        "🔗 <a href='https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222'>Apri Scheda Eurex BMW</a>\n\n"
        "<i>Screenshot automatico catturato alla chiusura.</i>"
    )
    
    send_telegram_photo(screenshot_path, caption)

if __name__ == "__main__":
    main()
