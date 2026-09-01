import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    # Endpoint statistiche Eurex per opzioni BMW (ID: 950222)
    stats_url = "https://www.eurex.com/api/v1/overallstatistics/950222"
    
    try:
        res = requests.get(stats_url, headers=headers, timeout=15)
        data = res.json()
        
        # Estrazione metriche generali
        underlying_price = data.get("underlyingClosingPrice", "N/D")
        tot_vol = data.get("totalVolume", "N/D")
        tot_oi = data.get("totalOpenInterest", "N/D")
        tot_pc = data.get("putCallRatio", "N/D")
        
        msg = f"📊 *REPORT GIORNALIERO EUREX — BMW*\n\n"
        msg += f"🔹 *Prezzo Sottostante:* `{underlying_price} €`\n"
        msg += f"🔹 *Volume Totale:* `{tot_vol}`\n"
        msg += f"🔹 *Open Interest Totale:* `{tot_oi}`\n"
        msg += f"🔹 *P/C Ratio Giornaliero:* `{tot_pc}`\n\n"
        msg += "📌 *Dettaglio Scadenze Principali:*\n"
        
        for item in data.get("items", [])[:3]:
            date = item.get("contractDate", "N/D")
            call_v = item.get("callVolume", 0)
            put_v = item.get("putVolume", 0)
            pc = item.get("putCallRatio", "N/D")
            call_oi = item.get("callOpenInterest", 0)
            put_oi = item.get("putOpenInterest", 0)
            
            msg += f"• *Scadenza {date}*\n"
            msg += f"  Vol: C `{call_v}` | P `{put_v}` (P/C: `{pc}`)\n"
            msg += f"  OI: C `{call_oi}` | P `{put_oi}`\n\n"
            
        send_telegram_message(msg)

    except Exception as e:
        send_telegram_message(f"⚠️ Errore nel recupero dati Eurex BMW: {str(e)}")

if __name__ == "__main__":
    main()
