import os
import sys
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ ERRORE: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non configurati nei Secrets di GitHub!")
        sys.exit(1)
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    
    resp = requests.post(url, json=payload, timeout=10)
    print(f"Risposta Telegram ({resp.status_code}): {resp.text}")
    
    if not resp.ok:
        print(f"❌ Telegram ha rifiutato il messaggio: {resp.text}")
        sys.exit(1)
    else:
        print("✅ Messaggio inviato con successo su Telegram!")

def main():
    print("Avvio recupero dati Eurex BMW...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    # Endpoint statistiche Eurex per opzioni BMW (ID: 950222)
    stats_url = "https://www.eurex.com/api/v1/overallstatistics/950222"
    
    try:
        res = requests.get(stats_url, headers=headers, timeout=15)
        print(f"Status Eurex: {res.status_code}")
        
        if res.status_code == 200 and "application/json" in res.headers.get("Content-Type", ""):
            data = res.json()
            price = data.get("underlyingClosingPrice", "N/D")
            vol = data.get("totalVolume", "N/D")
            oi = data.get("totalOpenInterest", "N/D")
            pc = data.get("putCallRatio", "N/D")
            
            msg = f"📊 <b>REPORT GIORNALIERO EUREX — BMW</b>\n\n"
            msg += f"🔹 <b>Prezzo Sottostante:</b> <code>{price} €</code>\n"
            msg += f"🔹 <b>Volume Totale:</b> <code>{vol}</code>\n"
            msg += f"🔹 <b>Open Interest Totale:</b> <code>{oi}</code>\n"
            msg += f"🔹 <b>P/C Ratio:</b> <code>{pc}</code>\n\n"
            msg += "📌 <b>Dettaglio Scadenze:</b>\n"
            
            for item in data.get("items", [])[:3]:
                d = item.get("contractDate", "N/D")
                cv = item.get("callVolume", 0)
                pv = item.get("putVolume", 0)
                pcr = item.get("putCallRatio", "N/D")
                coi = item.get("callOpenInterest", 0)
                poi = item.get("putOpenInterest", 0)
                
                msg += f"• <b>Scadenza {d}</b>\n"
                msg += f"  Vol: C <code>{cv}</code> | P <code>{pv}</code> (P/C: <code>{pcr}</code>)\n"
                msg += f"  OI: C <code>{coi}</code> | P <code>{poi}</code>\n\n"
        else:
            # Fallback informativo se l'endpoint pubblico richiede sessione
            msg = (
                "📊 <b>NOTIFICA ROUTINE EUREX BMW</b>\n\n"
                "I server Eurex richiedono una sessione browser attiva.\n"
                "🔗 <a href='https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222'>Apri scheda Eurex BMW</a>\n\n"
                "Invia lo screenshot della pagina per elaborare il report completo."
            )
            
        send_telegram(msg)
        
    except Exception as e:
        print(f"Eccezione: {e}")
        send_telegram(f"⚠️ <b>Avviso Eurex BMW:</b> Impossibile scaricare i dati automatici ({e}).")

if __name__ == "__main__":
    main()
