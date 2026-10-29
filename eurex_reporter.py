import os
import re
import sys
import csv
import json
import math
import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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


def parse_num(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if s in ["-", "n/a", "", "None"]:
        return 0.0
    try:
        return float(s)
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
    """
    Legge tutte le righe della tabella Eurex usando sia <td> che <th>,
    scorrendo verso l'alto (strike < 60€) e verso il basso (strike > 60€).
    """
    rows_data = {}

    def scrape_dom_table():
        # Legge tramite JavaScript tutte le righe tr (includendo sia td che th)
        raw_rows = page.evaluate("""() => {
            const trs = Array.from(document.querySelectorAll('table tbody tr'));
            return trs.map(tr => Array.from(tr.querySelectorAll('td, th')).map(c => c.innerText.trim()));
        }""")
        for cells in raw_rows:
            # Una riga valida di Prices/Quotes ha tra 13 e 17 celle totali
            if len(cells) < 13:
                continue
            # Trova la colonna Strike: è al centro e ha Vers. (0) come ultima colonna
            mid = len(cells) // 2
            strike_idx = -1
            for offset in [0, -1, 1, -2, 2]:
                idx = mid + offset
                if 6 <= idx < len(cells) - 6:
                    val = parse_num(cells[idx])
                    # Gli strike di BMW sono numeri come 0.01 oppure tra 10.00 e 150.00 con decimali .00 o .50
                    if val == 0.01 or (10.0 <= val <= 150.0 and (val * 2).is_integer() and "." in cells[idx]):
                        strike_idx = idx
                        break
            if strike_idx == -1:
                continue

            strike = parse_num(cells[strike_idx])
            # Allineamento simmetrico perfetto attorno a strike_idx (S):
            # Call (a sinistra di S): D.Settle(S-6), OI adj(S-5), Volume(S-4), Last(S-3), Bid(S-2), Ask(S-1)
            # Put (a destra di S):   Bid(S+1), Ask(S+2), Last(S+3), Volume(S+4), OI adj(S+5), D.Settle(S+6)
            rows_data[strike] = {
                "expiration": exp_label,
                "strike": strike,
                "c_settle": parse_num(cells[strike_idx - 6]),
                "c_oi": int(parse_num(cells[strike_idx - 5])),
                "c_vol": int(parse_num(cells[strike_idx - 4])),
                "c_last": parse_num(cells[strike_idx - 3]),
                "c_bid": parse_num(cells[strike_idx - 2]),
                "c_ask": parse_num(cells[strike_idx - 1]),
                "p_bid": parse_num(cells[strike_idx + 1]),
                "p_ask": parse_num(cells[strike_idx + 2]),
                "p_last": parse_num(cells[strike_idx + 3]),
                "p_vol": int(parse_num(cells[strike_idx + 4])),
                "p_oi": int(parse_num(cells[strike_idx + 5])),
                "p_settle": parse_num(cells[strike_idx + 6]),
            }

    scrape_dom_table()

    # Clicca la freccia SU dentro la prima riga tbody (per caricare gli strike 44€ -> 59€)
    for _ in range(8):
        clicked = page.evaluate("""() => {
            const firstRow = document.querySelector('table tbody tr:first-child');
            if (!firstRow) return false;
            const btn = firstRow.querySelector('svg, button, a, span[role="button"]');
            if (btn) {
                (btn.closest('button') || btn.parentElement || btn).click();
                return true;
            }
            return false;
        }""")
        if not clicked:
            break
        page.wait_for_timeout(700)
        scrape_dom_table()

    # Clicca la freccia GIÙ dentro l'ultima riga tbody (per caricare tutti gli strike fino a 75€+)
    for _ in range(10):
        clicked = page.evaluate("""() => {
            const rows = document.querySelectorAll('table tbody tr');
            if (!rows.length) return false;
            const lastRow = rows[rows.length - 1];
            const btn = lastRow.querySelector('svg, button, a, span[role="button"]');
            if (btn) {
                (btn.closest('button') || btn.parentElement || btn).click();
                return true;
            }
            return false;
        }""")
        if not clicked:
            break
        page.wait_for_timeout(700)
        scrape_dom_table()

    return sorted(rows_data.values(), key=lambda x: x["strike"])


def enrich_chain_with_greeks(chain: list):
    """Calcola Spot implicito, DTE, IV, Delta, Prob. Profitto, Break-Even e ROC per ogni riga."""
    spot_estimates = []
    for r in chain:
        k = r["strike"]
        if k == 0.01 and (r["c_last"] > 20 or r["c_settle"] > 20):
            spot_estimates.append(r["c_last"] or r["c_settle"])
        elif 48 <= k <= 68:
            c_p = r["c_settle"] or r["c_last"]
            p_p = r["p_settle"] or r["p_last"]
            if c_p > 0.1 and p_p > 0.1:
                spot_estimates.append(k + c_p - p_p)
    spot = float(np.median(spot_estimates)) if spot_estimates else 56.80

    today = datetime.utcnow()
    enriched = []
    for r in chain:
        if r["strike"] < 10.0:
            continue
        dte = 22
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
        enriched.append(row_out)

    return enriched, spot


def generate_hd_dashboard_image(enriched_chain: list, exp_label: str, spot: float, out_path: str):
    """Genera un'unica immagine HD in stile Dark Mode con TUTTI gli strike della scadenza."""
    rows = [r for r in enriched_chain if r["expiration"] == exp_label and 45.0 <= r["strike"] <= 70.0]
    if not rows:
        return False

    fig_h = max(8, len(rows) * 0.42 + 2.5)
    fig, ax = plt.subplots(figsize=(14, fig_h), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    ax.axis("off")

    dte = rows[0]["dte"]
    title = f"EUREX BMW — CATENA COMPLETA ({exp_label} | DTE: {dte} gg | Spot Implicito: {spot:.2f} €)"
    plt.suptitle(title, color="#58a6ff", fontsize=14, fontweight="bold", y=0.97)

    col_labels = [
        "C Settle", "C Bid", "C Ask", "C Vol", "C OI",
        "STRIKE",
        "P Bid", "P Ask", "P Settle", "P Vol", "P OI",
        "Put IV%", "Delta (Δ)", "Prob.Win", "BreakEven", "ROC Ann."
    ]

    table_data = []
    for r in rows:
        k = r["strike"]
        is_opt = -0.33 <= r["put_delta"] <= -0.18
        strike_str = f"★ {k:.2f} €" if is_opt else f"{k:.2f} €"
        table_data.append([
            f"{r['c_settle']:.2f}", f"{r['c_bid']:.2f}", f"{r['c_ask']:.2f}", f"{r['c_vol']}", f"{r['c_oi']:,}",
            strike_str,
            f"{r['p_bid']:.2f}", f"{r['p_ask']:.2f}", f"{r['p_settle']:.2f}", f"{r['p_vol']}", f"{r['p_oi']:,}",
            f"{r['put_iv_pct']:.1f}%", f"{r['put_delta']:.2f}", f"{r['put_prob_profit_pct']:.0f}%",
            f"{r['put_breakeven']:.2f}€", f"{r['put_roc_ann_pct']:.1f}%"
        ])

    tbl = ax.table(cellText=table_data, colLabels=col_labels, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1.0, 1.45)

    for (row_idx, col_idx), cell in tbl.get_celld().items():
        cell.set_edgecolor("#30363d")
        if row_idx == 0:
            cell.set_facecolor("#161b22")
            cell.set_text_props(color="#58a6ff", weight="bold")
        else:
            r = rows[row_idx - 1]
            k = r["strike"]
            is_atm = abs(k - spot) < 0.65
            is_sweet = -0.33 <= r["put_delta"] <= -0.18
            if col_idx == 5:
                cell.set_facecolor("#238636" if is_sweet else ("#1f6feb" if is_atm else "#21262d"))
                cell.set_text_props(color="#ffffff", weight="bold")
            elif is_sweet and col_idx > 5:
                cell.set_facecolor("#0f2d1a")
                cell.set_text_props(color="#7ee787", weight="bold" if col_idx == 15 else "normal")
            elif is_atm:
                cell.set_facecolor("#1c2536")
                cell.set_text_props(color="#e6edf3")
            else:
                cell.set_facecolor("#0d1117" if row_idx % 2 == 0 else "#161b22")
                cell.set_text_props(color="#c9d1d9")

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    plt.savefig(out_path, dpi=160, facecolor="#0d1117")
    plt.close(fig)
    return True


def build_quant_report(exp_date_str: str, enriched_chain: list, spot: float) -> str:
    valid_chain = [r for r in enriched_chain if r["expiration"] == exp_date_str]
    if not valid_chain:
        return "⚠️ Nessuna riga estratta."

    dte = valid_chain[0]["dte"]
    T = dte / 365.0
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
    iv_atm = atm_row["put_iv_pct"] if atm_row["put_iv_pct"] > 0 else 32.0
    exp_move = spot * (iv_atm / 100.0) * math.sqrt(T)

    msg = f"📊 <b>QUANT REPORT EUREX — BMW</b>\n"
    msg += f"📅 <b>Scadenza:</b> <code>{exp_date_str}</code> ({dte} gg) | <b>Estratti:</b> <code>{len(valid_chain)} strike</code>\n"
    msg += f"💶 <b>Spot Implicito:</b> <code>{spot:.2f} €</code> | <b>FV Morningstar:</b> <code>{MORNINGSTAR_FV:.0f} €</code>\n\n"
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
        new_pmc = (PORTFOLIO_PMC + r["put_breakeven"]) / 2.0
        tag = "⭐" if -0.33 <= r["put_delta"] <= -0.18 else "🔹"
        msg += (
            f"{tag} <b>Put {k:.1f}€</b> | Settle: <code>{prem:.2f}€</code> (B/A: {r['p_bid']:.2f}/{r['p_ask']:.2f})\n"
            f"   Δ: <code>{r['put_delta']:.2f}</code> | Prob: <code>{r['put_prob_profit_pct']:.0f}%</code> | "
            f"BE: <code>{r['put_breakeven']:.2f}€</code> | Ann: <b>{r['put_roc_ann_pct']:.1f}%</b> | PMC: <code>{new_pmc:.2f}€</code>\n"
        )

    return msg


def main():
    target_url = "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222"
    dashboard_img = "bmw_full_chain_hd.png"
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

        try:
            cookie_btn = page.locator("button:has-text('DECLINE ALL'), button:has-text('ACCEPT ALL')")
            if cookie_btn.count() > 0:
                cookie_btn.first.click(timeout=3000)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        exp_dates = []
        try:
            for b_text in page.locator("button").all_inner_texts():
                m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", b_text)
                if m and m.group(1) not in exp_dates:
                    exp_dates.append(m.group(1))
        except Exception:
            pass

        if exp_dates:
            front_exp = exp_dates[0]

        for idx, exp_label in enumerate(exp_dates[:2]):
            if idx > 0:
                try:
                    page.locator(f"button:has-text('{exp_label}')").first.click(timeout=2000)
                    page.wait_for_timeout(2000)
                except Exception:
                    continue
            chain_part = extract_chain_for_current_tab(page, exp_label)
            all_chains.extend(chain_part)

        browser.close()

    enriched, spot = enrich_chain_with_greeks(all_chains)

    # 1. Genera e invia un'unica tabella grafica HD con TUTTI gli strike (da 45€ a 70€)
    if generate_hd_dashboard_image(enriched, front_exp, spot, dashboard_img):
        send_telegram_photo(
            dashboard_img,
            f"📊 <b>TABELLA COMPLETA HD — BMW ({front_exp})</b>\n<i>Tutti gli strike da 45€ a 70€ con Greche e Rendimenti</i>"
        )

    # 2. Invia il Report Quantitativo testuale
    quant_report = build_quant_report(front_exp, enriched, spot)
    send_telegram_message(quant_report)

    # 3. Salva e invia il CSV completo e riallineato
    if enriched:
        pd.DataFrame(enriched).to_csv(csv_path, index=False)
        send_telegram_document(
            csv_path,
            f"📂 <b>bmw_eurex_chain.csv</b> ({len(enriched)} strike totali estratti)"
        )


if __name__ == "__main__":
    main()
