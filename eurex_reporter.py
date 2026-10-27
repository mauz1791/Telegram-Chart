import os
import re
import sys
import math
import requests
import numpy as np
from datetime import datetime
from scipy.stats import norm
from scipy.optimize import brentq
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Parametri di portafoglio per il calcolo del nuovo PMC in caso di assegnazione
PORTFOLIO_SHARES = 100
PORTFOLIO_PMC = 74.00
MORNINGSTAR_FV = 67.00
RISK_FREE_RATE = 0.025  # Tasso BCE ~2.5%


def send_telegram_photo(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as img:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML"
        }
        resp = requests.post(url, data=payload, files={"photo": img}, timeout=30)
    if not resp.ok:
        print(f"Errore invio foto: {resp.text}")


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    resp = requests.post(url, json=payload, timeout=20)
    if not resp.ok:
        print(f"Errore invio messaggio: {resp.text}")
        sys.exit(1)


def parse_num(val: str) -> float:
    if not val:
        return 0.0
    val = val.strip().replace(",", "")
    if val in ["-", "n/a", ""]:
        return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def compute_put_greeks(S: float, K: float, T: float, r: float, price: float):
    intrinsic = max(0.0, K - S * math.exp(-r * T))
    if price <= intrinsic + 1e-4 or T <= 0:
        return 0.0, 0.0, 0.0
    try:
        iv = brentq(lambda x: bs_put_price(S, K, T, r, x) - price, 0.01, 3.0)
    except Exception:
        return 0.0, 0.0, 0.0

    d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    delta = norm.cdf(d1) - 1.0
    be = K - price
    d2_be = (math.log(S / be) + (r - 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    prob_profit = norm.cdf(d2_be) * 100.0
    return iv * 100.0, delta, prob_profit


def extract_chain_from_dom(page):
    """Estrae le righe della catena opzioni dalla tabella Prices/Quotes di Eurex."""
    rows_data = {}
    exp_date_str = None

    # Rileva la data di scadenza attiva (es. 20/11/2026)
    try:
        buttons = page.locator("button").all_inner_texts()
        for b in buttons:
            m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", b)
            if m:
                exp_date_str = m.group(1)
                break
    except Exception:
        pass

    # Scorre la tabella per raccogliere tutti gli strike
    for _ in range(5):
        rows = page.locator("table tbody tr, tr").all()
        for r in rows:
            try:
                cells = [c.inner_text().strip() for c in r.locator("td").all()]
                # Struttura tabella Prices/Quotes Eurex (13-14 colonne):
                # Call: Settle(0), OI(1), Vol(2), Last(3), Bid(4), Ask(5)
                # Strike(6)
                # Put: Bid(7), Ask(8), Last(9), Vol(10), OI(11), Settle(12)
                if len(cells) >= 13:
                    strike = parse_num(cells[6])
                    if strike > 0:
                        rows_data[strike] = {
                            "strike": strike,
                            "c_settle": parse_num(cells[0]),
                            "c_oi": int(parse_num(cells[1])),
                            "c_vol": int(parse_num(cells[2])),
                            "c_last": parse_num(cells[3]),
                            "c_bid": parse_num(cells[4]),
                            "c_ask": parse_num(cells[5]),
                            "p_bid": parse_num(cells[7]),
                            "p_ask": parse_num(cells[8]),
                            "p_last": parse_num(cells[9]),
                            "p_vol": int(parse_num(cells[10])),
                            "p_oi": int(parse_num(cells[11])),
                            "p_settle": parse_num(cells[12]),
                        }
            except Exception:
                continue

        # Clicca la freccia giù della tabella per caricare gli strike successivi
        try:
            down_arrow = page.locator("button:has(svg), .arrow-down, [aria-label*='down' i]")
            if down_arrow.count() > 0:
                down_arrow.last.click(timeout=1000)
                page.wait_for_timeout(800)
        except Exception:
            break

    return exp_date_str, sorted(rows_data.values(), key=lambda x: x["strike"])


def build_quant_report(exp_date_str: str, chain: list) -> str:
    if not chain:
        return (
            "📊 <b>REPORT EUREX BMW</b>\n\n"
            "⚠️ Tabella opzioni in aggiornamento sui server Eurex.\n"
            "Consulta lo screenshot allegato per i dettagli visivi."
        )

    # Calcolo DTE (Giorni a scadenza)
    today = datetime.utcnow()
    dte = 30
    if exp_date_str:
        try:
            exp_dt = datetime.strptime(exp_date_str, "%d/%m/%Y")
            dte = max(1, (exp_dt - today).days)
        except Exception:
            pass
    T = dte / 365.0

    # Stima Prezzo Sottostante (da strike 0.01 o Put-Call Parity S = K + C - P)
    spot_estimates = []
    for row in chain:
        k = row["strike"]
        if k == 0.01 and (row["c_last"] > 20 or row["c_settle"] > 20):
            spot_estimates.append(row["c_last"] or row["c_settle"])
        elif 45 <= k <= 70:
            c_p = row["c_settle"] or row["c_last"]
            p_p = row["p_settle"] or row["p_last"]
            if c_p > 0.2 and p_p > 0.2:
                spot_estimates.append(k + c_p - p_p)

    spot = float(np.median(spot_estimates)) if spot_estimates else 57.64

    # Filtra solo gli strike standard (> 10 €)
    valid_chain = [r for r in chain if r["strike"] >= 10.0]

    tot_c_oi = sum(r["c_oi"] for r in valid_chain)
    tot_p_oi = sum(r["p_oi"] for r in valid_chain)
    tot_c_vol = sum(r["c_vol"] for r in valid_chain)
    tot_p_vol = sum(r["p_vol"] for r in valid_chain)
    pc_oi = (tot_p_oi / tot_c_oi) if tot_c_oi > 0 else 0.0

    # Call Wall & Put Wall
    call_wall = max(valid_chain, key=lambda r: r["c_oi"])
    put_wall = max(valid_chain, key=lambda r: r["p_oi"])

    # Calcolo Max Pain
    pain_list = []
    for s_row in valid_chain:
        s = s_row["strike"]
        c_loss = sum(max(0.0, s - r["strike"]) * r["c_oi"] for r in valid_chain)
        p_loss = sum(max(0.0, r["strike"] - s) * r["p_oi"] for r in valid_chain)
        pain_list.append((s, c_loss + p_loss))
    max_pain_strike = min(pain_list, key=lambda x: x[1])[0]

    # Calcolo IV ATM & Expected Move (1 SD)
    atm_row = min(valid_chain, key=lambda r: abs(r["strike"] - spot))
    atm_put_price = atm_row["p_settle"] or atm_row["p_last"]
    iv_atm, _, _ = compute_put_greeks(spot, atm_row["strike"], T, RISK_FREE_RATE, atm_put_price)
    if iv_atm <= 0:
        iv_atm = 32.0
    exp_move = spot * (iv_atm / 100.0) * math.sqrt(T)
    low_1sd = spot - exp_move
    high_1sd = spot + exp_move

    # Costruzione Messaggio Telegram
    msg = f"📊 <b>QUANT REPORT EUREX — BMW</b>\n"
    msg += f"📅 <b>Scadenza:</b> <code>{exp_date_str or 'Front-Month'}</code> ({dte} gg)\n"
    msg += f"💶 <b>Spot Implicito:</b> <code>{spot:.2f} €</code> | <b>FV Morningstar:</b> <code>{MORNINGSTAR_FV:.0f} €</code>\n\n"

    msg += "🧱 <b> ARCHITETTURA ISTITUZIONALE</b>\n"
    msg += f"• <b>Max Pain:</b> <code>{max_pain_strike:.2f} €</code>\n"
    msg += f"• <b>Put Wall (Supporto):</b> <code>{put_wall['strike']:.2f} €</code> ({put_wall['p_oi']:,} OI)\n"
    msg += f"• <b>Call Wall (Tetto):</b> <code>{call_wall['strike']:.2f} €</code> ({call_wall['c_oi']:,} OI)\n"
    msg += f"• <b>OI Totale:</b> C <code>{tot_c_oi:,}</code> | P <code>{tot_p_oi:,}</code> (P/C: <code>{pc_oi:.2f}</code>)\n"
    msg += f"• <b>Volumi Oggi:</b> C <code>{tot_c_vol:,}</code> | P <code>{tot_p_vol:,}</code>\n\n"

    msg += "📐 <b>VOLATILITÀ E MOVIMENTO ATTESO (1 SD - 68%)</b>\n"
    msg += f"• <b>IV ATM:</b> <code>{iv_atm:.1f}%</code> | <b>Expected Move:</b> <code>±{exp_move:.2f} €</code>\n"
    msg += f"• <b>Tunnel Statistico:</b> <code>{low_1sd:.2f} €</code> ↔️ <code>{high_1sd:.2f} €</code>\n\n"

    msg += "🎯 <b>SCANNER VENDITA PUT (WHEEL STRATEGY)</b>\n"
    candidates = [
        r for r in valid_chain
        if (spot - 7.0) <= r["strike"] <= (spot + 0.5)
    ]

    for r in candidates:
        k = r["strike"]
        prem = r["p_settle"] or r["p_last"]
        if prem <= 0.10:
            continue
        iv, delta, prob_prof = compute_put_greeks(spot, k, T, RISK_FREE_RATE, prem)
        be = k - prem
        roc = (prem / be) * 100.0
        ann_roc = roc * (365.0 / dte)
        new_pmc = (PORTFOLIO_PMC + be) / 2.0
        tp_50 = prem * 0.50
        bid_str = f"{r['p_bid']:.2f}" if r["p_bid"] > 0 else "-"
        ask_str = f"{r['p_ask']:.2f}" if r["p_ask"] > 0 else "-"

        tag = "⭐" if -0.32 <= delta <= -0.20 else "🔹"
        msg += (
            f"{tag} <b>Put {k:.1f} €</b> | Premio: <code>{prem:.2f} €</code> (B/A: {bid_str}/{ask_str})\n"
            f"   Δ: <code>{delta:.2f}</code> | Prob. Profit: <code>{prob_prof:.1f}%</code> | BE: <code>{be:.2f} €</code>\n"
            f"   ROC Annuo: <b>{ann_roc:.1f}%</b> | TP 50%: <code>{tp_50:.2f} €</code> | PMC 200az: <code>{new_pmc:.2f} €</code>\n"
        )

    return msg


def main():
    target_url = "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222"
    screenshot_path = "eurex_bmw.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 1400},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(target_url, wait_until="networkidle", timeout=60000)

        # Accetta cookie banner
        try:
            cookie_btn = page.locator("button:has-text('Accept'), button:has-text('Accetta'), #cookie-accept-all")
            if cookie_btn.count() > 0:
                cookie_btn.first.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        page.wait_for_timeout(4000)
        page.screenshot(path=screenshot_path, full_page=False)

        exp_date_str, chain = extract_chain_from_dom(page)
        browser.close()

    caption = (
        "📸 <b>EUREX BMW — SCREENSHOT CATENA OPZIONI</b>\n"
        "🔗 <a href='https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222'>Apri Scheda Eurex BMW</a>"
    )
    send_telegram_photo(screenshot_path, caption)

    report_text = build_quant_report(exp_date_str, chain)
    send_telegram_message(report_text)


if __name__ == "__main__":
    main()
