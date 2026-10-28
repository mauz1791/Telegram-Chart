import os
import re
import sys
import csv
import math
import requests
import numpy as np
from datetime import datetime
from scipy.stats import norm
from scipy.optimize import brentq
from playwright.sync_api import sync_playwright

raw_token = os.environ.get("TELEGRAM_TOKEN", "").strip()
if raw_token.lower().startswith("bot"):
    raw_token = raw_token[3:].strip()

TELEGRAM_TOKEN = raw_token
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

PORTFOLIO_PMC = 74.00
MORNINGSTAR_FV = 67.00
RISK_FREE_RATE = 0.025

if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ ERRORE: Controlla TELEGRAM_TOKEN e TELEGRAM_CHAT_ID nei Secrets.")
    sys.exit(1)


def send_telegram_photo(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as img:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": img},
            timeout=40
        )
    if not resp.ok:
        send_telegram_document(photo_path, caption)


def send_telegram_document(file_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(file_path, "rb") as doc:
        requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"document": doc},
            timeout=40
        )


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text[i:i + 4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=20
        )


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


def extract_chain_for_current_tab(page, exp_label: str) -> list:
    """Estrae tutte le righe della scadenza selezionata identificando dinamicamente la colonna Strike."""
    rows_data = {}

    def scrape_visible_rows():
        headers = [h.inner_text().strip().lower() for h in page.locator("table th").all()]
        s_idx = -1
        for idx, h in enumerate(headers):
            if "strike" in h:
                s_idx = idx
                break

        for r in page.locator("table tbody tr").all():
            try:
                cells = [c.inner_text().strip() for c in r.locator("td").all()]
                if len(cells) < 11:
                    continue
                strike_col = s_idx if (0 <= s_idx < len(cells)) else (len(cells) // 2)
                strike = parse_num(cells[strike_col])
                if strike <= 0:
                    continue

                rows_data[strike] = {
                    "expiration": exp_label,
                    "strike": strike,
                    "c_settle": parse_num(cells[strike_col - 6]) if strike_col >= 6 else 0.0,
                    "c_oi": int(parse_num(cells[strike_col - 5])) if strike_col >= 5 else 0,
                    "c_vol": int(parse_num(cells[strike_col - 4])) if strike_col >= 4 else 0,
                    "c_last": parse_num(cells[strike_col - 3]) if strike_col >= 3 else 0.0,
                    "c_bid": parse_num(cells[strike_col - 2]) if strike_col >= 2 else 0.0,
                    "c_ask": parse_num(cells[strike_col - 1]) if strike_col >= 1 else 0.0,
                    "p_bid": parse_num(cells[strike_col + 1]) if len(cells) > strike_col + 1 else 0.0,
                    "p_ask": parse_num(cells[strike_col + 2]) if len(cells) > strike_col + 2 else 0.0,
                    "p_last": parse_num(cells[strike_col + 3]) if len(cells) > strike_col + 3 else 0.0,
                    "p_vol": int(parse_num(cells[strike_col + 4])) if len(cells) > strike_col + 4 else 0,
                    "p_oi": int(parse_num(cells[strike_col + 5])) if len(cells) > strike_col + 5 else 0,
                    "p_settle": parse_num(cells[strike_col + 6]) if len(cells) > strike_col + 6 else 0.0,
                }
            except Exception:
                continue

    scrape_visible_rows()

    # Scorre verso l'alto per catturare gli strike OTM bassi (44€ - 58€)
    for _ in range(5):
        try:
            arrows = page.locator("table svg, button svg").all()
            if arrows:
                arrows[0].click(timeout=1000)
                page.wait_for_timeout(600)
                scrape_visible_rows()
        except Exception:
            break

    # Scorre verso il basso per catturare gli strike alti (58€ - 75€)
    for _ in range(7):
        try:
            arrows = page.locator("table svg, button svg").all()
            if arrows:
                arrows[-1].click(timeout=1000)
                page.wait_for_timeout(600)
                scrape_visible_rows()
        except Exception:
            break

    return sorted(rows_data.values(), key=lambda x: x["strike"])


def save_enriched_csv(chain: list, filepath: str, spot: float):
    if not chain:
        return
    fieldnames = [
        "expiration", "dte", "spot_implied", "strike",
        "c_settle", "c_bid", "c_ask", "c_last", "c_vol", "c_oi",
        "p_bid", "p_ask", "p_last", "p_settle", "p_vol", "p_oi",
        "put_iv_pct", "put_delta", "put_prob_profit_pct", "put_breakeven", "put_roc_ann_pct"
    ]
    today = datetime.utcnow()
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in chain:
            if r["strike"] < 10.0:
                continue
            dte = 25
            try:
                exp_dt = datetime.strptime(r["expiration"], "%d/%m/%Y")
                dte = max(1, (exp_dt - today).days)
            except Exception:
                pass
            T = dte / 365.0
            prem = r["p_settle"] or r["p_last"]
            iv, delta, prob_prof = compute_put_greeks(spot, r["strike"], T, RISK_FREE_RATE, prem)
            be = (r["strike"] - prem) if prem > 0 else r["strike"]
            ann_roc = (((prem / be) * 100.0) * (365.0 / dte)) if (prem > 0 and be > 0) else 0.0

            row_out = dict(r)
            row_out.update({
                "dte": dte,
                "spot_implied": round(spot, 2),
                "put_iv_pct": round(iv, 2),
                "put_delta": round(delta, 3),
                "put_prob_profit_pct": round(prob_prof, 1),
                "put_breakeven": round(be, 2),
                "put_roc_ann_pct": round(ann_roc, 2)
            })
            writer.writerow(row_out)


def build_quant_report(exp_date_str: str, chain: list):
    valid_chain = [r for r in chain if r["strike"] >= 10.0 and r["expiration"] == exp_date_str]
    if not valid_chain:
        return "⚠️ Nessuna riga standard estratta dalla tabella Eurex.", 56.80

    today = datetime.utcnow()
    dte = 23
    try:
        exp_dt = datetime.strptime(exp_date_str, "%d/%m/%Y")
        dte = max(1, (exp_dt - today).days)
    except Exception:
        pass
    T = dte / 365.0

    spot_estimates = []
    for row in valid_chain:
        k = row["strike"]
        if 48 <= k <= 68:
            c_p = row["c_settle"] or row["c_last"]
            p_p = row["p_settle"] or row["p_last"]
            if c_p > 0.1 and p_p > 0.1:
                spot_estimates.append(k + c_p - p_p)
    spot = float(np.median(spot_estimates)) if spot_estimates else 56.80

    tot_c_oi = sum(r["c_oi"] for r in valid_chain)
    tot_p_oi = sum(r["p_oi"] for r in valid_chain)
    tot_c_vol = sum(r["c_vol"] for r in valid_chain)
    tot_p_vol = sum(r["p_vol"] for r in valid_chain)
    pc_oi = (tot_p_oi / tot_c_oi) if tot_c_oi > 0 else 0.0

    call_wall = max(valid_chain, key=lambda r: r["c_oi"])
    put_wall = max(valid_chain, key=lambda r: r["p_oi"])

    pain_list = []
    for s_row in valid_chain:
        s = s_row["strike"]
        c_loss = sum(max(0.0, s - r["strike"]) * r["c_oi"] for r in valid_chain)
        p_loss = sum(max(0.0, r["strike"] - s) * r["p_oi"] for r in valid_chain)
        pain_list.append((s, c_loss + p_loss))
    max_pain_strike = min(pain_list, key=lambda x: x[1])[0]

    atm_row = min(valid_chain, key=lambda r: abs(r["strike"] - spot))
    atm_put_price = atm_row["p_settle"] or atm_row["p_last"]
    iv_atm, _, _ = compute_put_greeks(spot, atm_row["strike"], T, RISK_FREE_RATE, atm_put_price)
    if iv_atm <= 0:
        iv_atm = 32.0
    exp_move = spot * (iv_atm / 100.0) * math.sqrt(T)

    msg = f"📊 <b>QUANT REPORT EUREX — BMW</b>\n"
    msg += f"📅 <b>Scadenza:</b> <code>{exp_date_str}</code> ({dte} gg)\n"
    msg += f"💶 <b>Spot Implicito:</b> <code>{spot:.2f} €</code> | <b>FV:</b> <code>{MORNINGSTAR_FV:.0f} €</code>\n\n"
    msg += "🧱 <b>ARCHITETTURA ISTITUZIONALE</b>\n"
    msg += f"• <b>Max Pain:</b> <code>{max_pain_strike:.2f} €</code>\n"
    msg += f"• <b>Put Wall:</b> <code>{put_wall['strike']:.2f} €</code> ({put_wall['p_oi']:,} OI)\n"
    msg += f"• <b>Call Wall:</b> <code>{call_wall['strike']:.2f} €</code> ({call_wall['c_oi']:,} OI)\n"
    msg += f"• <b>OI Totale:</b> C <code>{tot_c_oi:,}</code> | P <code>{tot_p_oi:,}</code> (P/C: <code>{pc_oi:.2f}</code>)\n"
    msg += f"• <b>Volumi Oggi:</b> C <code>{tot_c_vol:,}</code> | P <code>{tot_p_vol:,}</code>\n"
    msg += f"• <b>Expected Move (1 SD):</b> <code>±{exp_move:.2f} €</code> (<code>{spot - exp_move:.2f}€</code> ↔️ <code>{spot + exp_move:.2f}€</code>)\n\n"

    msg += "🎯 <b>SCANNER VENDITA PUT (Front-Month)</b>\n"
    for r in valid_chain:
        k = r["strike"]
        if not ((spot - 6.5) <= k <= (spot + 0.5)):
            continue
        prem = r["p_settle"] or r["p_last"]
        if prem <= 0.10:
            continue
        iv, delta, prob_prof = compute_put_greeks(spot, k, T, RISK_FREE_RATE, prem)
        be = k - prem
        ann_roc = ((prem / be) * 100.0) * (365.0 / dte)
        new_pmc = (PORTFOLIO_PMC + be) / 2.0
        tag = "⭐" if -0.32 <= delta <= -0.20 else "🔹"
        msg += (
            f"{tag} <b>Put {k:.1f}€</b> | Settle: <code>{prem:.2f}€</code> (B/A: {r['p_bid']:.2f}/{r['p_ask']:.2f})\n"
            f"   Δ: <code>{delta:.2f}</code> | Prob: <code>{prob_prof:.0f}%</code> | BE: <code>{be:.2f}€</code> | "
            f"Ann: <b>{ann_roc:.1f}%</b> | PMC: <code>{new_pmc:.2f}€</code>\n"
        )

    return msg, spot


def main():
    target_url = "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222"
    screenshot_path = "eurex_bmw_full.png"
    csv_path = "bmw_eurex_chain.csv"

    all_chains = []
    front_exp = "20/11/2026"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1400, "height": 2400},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(target_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        # Chiude il banner dei cookie
        try:
            cookie_btn = page.locator(
                "button:has-text('DECLINE ALL'), button:has-text('ACCEPT ALL'), "
                "button:has-text('Accept'), button:has-text('Decline')"
            )
            if cookie_btn.count() > 0:
                cookie_btn.first.click(timeout=3000)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        page.add_style_tag(content="""
            #onetrust-banner-sdk, .cookie-banner, [class*='cookie' i] { display: none !important; }
        """)
        page.wait_for_timeout(1000)

        # Identifica i pulsanti delle prime 2 scadenze (es. 20/11/2026 e 18/12/2026)
        exp_Dates = []
        try:
            for b_text in page.locator("button").all_inner_texts():
                m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", b_text)
                if m and m.group(1) not in exp_Dates:
                    exp_Dates.append(m.group(1))
        except Exception:
            pass

        if exp_Dates:
            front_exp = exp_Dates[0]

        #Scatta subito lo screenshot a pagina intera della scadenza principale
        page.screenshot(path=screenshot_path, full_page=True)

        # Estrae la prima scadenza (es. Novembre) e la seconda (es. Dicembre)
        for idx, exp_label in enumerate(exp_Dates[:2]):
            if idx > 0:
                try:
                    page.locator(f"button:has-text('{exp_label}')").first.click(timeout=2000)
                    page.wait_for_timeout(2000)
                except Exception:
                    continue
            chain_part = extract_chain_for_current_tab(page, exp_label)
            all_chains.extend(chain_part)

        browser.close()

    send_telegram_photo(
        screenshot_path,
        "📸 <b>EUREX BMW — SCREENSHOT PAGINA INTERA</b>"
    )

    quant_report, spot = build_quant_report(front_exp, all_chains)
    send_telegram_message(quant_report)

    if all_chains:
        save_enriched_csv(all_chains, csv_path, spot)
        send_telegram_document(
            csv_path,
            "📂 <b>bmw_eurex_chain.csv</b> — <i>Inoltra questo file a Gemini Pro per l'analisi strategica completa.</i>"
        )


if __name__ == "__main__":
    main()
