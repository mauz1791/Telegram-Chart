import os
import re
import sys
import csv
import math
import traceback
import requests
import numpy as np
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

# Tua posizione aperta in portafoglio
OPEN_PUT_EXP = "20/11/2026"
OPEN_PUT_STRIKE = 57.00
OPEN_PUT_ENTRY = 1.45

if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("ERRORE: Controlla TELEGRAM_TOKEN e TELEGRAM_CHAT_ID nei Secrets.")
    sys.exit(1)


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 3800):
        chunk = text[i:i + 3800]
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=20
        )
        # Fallback automatico: se Telegram rifiuta l'HTML (errore 400), invia in testo puro
        if not resp.ok:
            clean_txt = re.sub(r"<[^>]+>", "", chunk)
            requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": clean_txt},
                timeout=20
            )


def send_telegram_photo(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as img:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
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
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": doc},
            timeout=40
        )


def parse_num(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if s in ["-", "n/a", "", "None"]:
        return 0.0
    m = re.search(r"[-+]?\d*\.?\d+", s)
    return float(m.group(0)) if m else 0.0


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


def extract_current_view(page, exp_label: str) -> dict:
    """
    Legge le 3 tabelle di Eurex SENZA filtrare le celle vuote:
    - Tabella Call (11 colonne): [-7]=D.Settle, [-6]=OI, [-5]=Vol, [-4]=Last, [-3]=Bid, [-2]=Ask
    - Tabella Strike (1 colonna): [0]=Strike (es. 54.00 .. 68.00)
    - Tabella Put (11 colonne): [1]=Bid, [2]=Ask, [3]=Last, [4]=Vol, [5]=OI, [6]=D.Settle
    """
    raw = page.evaluate(r"""() => {
        const trs = Array.from(document.querySelectorAll('tr')).map(tr =>
            Array.from(tr.querySelectorAll('td, th')).map(c => c.innerText.trim())
        );

        const strikes = [];
        const elevenColRows = [];

        for (const r of trs) {
            // Righe della tabella centrale Strike (1 sola cella con formato XX.00 o XX.50)
            const nonEmpty = r.filter(x => x !== '');
            if (nonEmpty.length === 1 && /^\d{2,3}\.(00|50)$/.test(nonEmpty[0])) {
                const v = parseFloat(nonEmpty[0]);
                if (v >= 25 && v <= 120) strikes.push(v);
                continue;
            }
            // Righe dati delle tabelle Call e Put (hanno tra 9 e 12 celle totali incluse le vuote)
            if (r.length >= 9 && r.length <= 13) {
                const joined = r.join(' ').toLowerCase();
                if (!joined.includes('settle') && !joined.includes('volume') && !joined.includes('total')) {
                    elevenColRows.push(r);
                }
            }
        }
        return { strikes: strikes, dataRows: elevenColRows };
    }""")

    strikes = raw.get("strikes", [])
    data_rows = raw.get("dataRows", [])
    n = len(strikes)
    extracted = {}

    if n == 0:
        return extracted

    # Le prime N righe dati appartengono alla tabella Call, le successive N alla tabella Put
    call_rows = data_rows[:n] if len(data_rows) >= n else []
    put_rows = data_rows[n:2 * n] if len(data_rows) >= 2 * n else []

    for i, k in enumerate(strikes):
        c_r = call_rows[i] if i < len(call_rows) else []
        p_r = put_rows[i] if i < len(put_rows) else []

        # Call: se l'ultima cella è vuota (barra verde), scorre di 1 a sinistra
        c_end = len(c_r) - 1 if (c_r and c_r[-1] == "") else len(c_r)
        # Put: se la prima cella è vuota (barra verde), parte dall'indice 1
        p_start = 1 if (p_r and p_r[0] == "") else 0

        def get_c(idx):
            return parse_num(c_r[idx]) if 0 <= idx < len(c_r) else 0.0

        def get_p(idx):
            return parse_num(p_r[idx]) if 0 <= idx < len(p_r) else 0.0

        extracted[k] = {
            "expiration": exp_label,
            "strike": float(k),
            "c_settle": get_c(c_end - 6),
            "c_oi": int(get_c(c_end - 5)),
            "c_vol": int(get_c(c_end - 4)),
            "c_last": get_c(c_end - 3),
            "c_bid": get_c(c_end - 2),
            "c_ask": get_c(c_end - 1),
            "p_bid": get_p(p_start + 0),
            "p_ask": get_p(p_start + 1),
            "p_last": get_p(p_start + 2),
            "p_vol": int(get_p(p_start + 3)),
            "p_oi": int(get_p(p_start + 4)),
            "p_settle": get_p(p_start + 5),
        }

    return extracted


def click_arrow(page, direction: str) -> bool:
    clicked = page.evaluate(r"""(dir) => {
        const svgs = Array.from(document.querySelectorAll('table svg'));
        if (svgs.length === 0) return false;
        const target = (dir === 'up') ? svgs[0] : svgs[svgs.length - 1];
        const btn = target.closest('button, td, th, tr, div') || target;
        if (typeof btn.click === 'function') btn.click();
        return true;
    }""", direction)
    if clicked:
        page.wait_for_timeout(800)
    return bool(clicked)


def scrape_expiration(page, exp_label: str):
    rows_data = {}
    rows_data.update(extract_current_view(page, exp_label))

    for _ in range(4):
        prev_min = min(rows_data.keys()) if rows_data else 999
        if not click_arrow(page, "up"):
            break
        rows_data.update(extract_current_view(page, exp_label))
        if (min(rows_data.keys()) if rows_data else 999) >= prev_min:
            break

    for _ in range(5):
        prev_max = max(rows_data.keys()) if rows_data else 0
        if not click_arrow(page, "down"):
            break
        rows_data.update(extract_current_view(page, exp_label))
        if (max(rows_data.keys()) if rows_data else 0) <= prev_max:
            break

    return sorted(rows_data.values(), key=lambda x: x["strike"])


def generate_dashboard_chart(chain: list, exp_date_str: str, spot: float, max_pain: float, out_path: str):
    valid = [r for r in chain if r["expiration"] == exp_date_str and 45.0 <= r["strike"] <= 74.0]
    if not valid:
        return False

    strikes = [r["strike"] for r in valid]
    c_oi = [r["c_oi"] for r in valid]
    p_oi = [r["p_oi"] for r in valid]

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    x = np.arange(len(strikes))
    width = 0.38

    ax.bar(x - width / 2, c_oi, width, label="Call OI (Resistenze)", color="#00d2ff", alpha=0.85)
    ax.bar(x + width / 2, p_oi, width, label="Put OI (Supporti)", color="#ff4b4b", alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{s:.0f}€" if s.is_integer() else f"{s:.1f}€" for s in strikes], rotation=45, fontsize=9)
    ax.set_ylabel("Open Interest (Contratti)", fontsize=10)
    ax.set_title(
        f"BMW Eurex Options ({exp_date_str}) | Spot: {spot:.2f}€ | Max Pain: {max_pain:.1f}€",
        fontsize=12, fontweight="bold", pad=15
    )
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.legend(loc="upper right", fontsize=10)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)
    return True


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
            dte = 21
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
    valid_chain = [r for r in chain if r["expiration"] == exp_date_str]
    if not valid_chain:
        return "⚠️ Nessuna riga estratta.", 57.50, 57.0

    today = datetime.utcnow()
    dte = 21
    try:
        exp_dt = datetime.strptime(exp_date_str, "%d/%m/%Y")
        dte = max(1, (exp_dt - today).days)
    except Exception:
        pass
    T = dte / 365.0

    spot_estimates = []
    for row in valid_chain:
        k = row["strike"]
        if 50 <= k <= 66:
            c_p = row["c_settle"] or row["c_last"]
            p_p = row["p_settle"] or row["p_last"]
            if c_p > 0.05 and p_p > 0.05:
                spot_estimates.append(k + c_p - p_p)
    spot = float(np.median(spot_estimates)) if spot_estimates else 57.50

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
        iv_atm = 30.0
    exp_move = spot * (iv_atm / 100.0) * math.sqrt(T)

    msg = f"📊 <b>QUANT REPORT EUREX — BMW</b>\n"
    msg += f"📅 <b>Scadenza:</b> <code>{exp_date_str}</code> ({dte} gg) | <b>Strike Lette:</b> <code>{len(valid_chain)}</code>\n"
    msg += f"💶 <b>Spot Implicito:</b> <code>{spot:.2f} €</code> | <b>FV:</b> <code>{MORNINGSTAR_FV:.0f} €</code>\n\n"

    my_put = next((r for r in valid_chain if abs(r["strike"] - OPEN_PUT_STRIKE) < 0.01), None)
    if my_put and exp_date_str == OPEN_PUT_EXP:
        curr_p = my_put["p_last"] or my_put["p_settle"] or my_put["p_ask"]
        pnl_eur = (OPEN_PUT_ENTRY - curr_p) * 100.0
        pnl_pct = ((OPEN_PUT_ENTRY - curr_p) / OPEN_PUT_ENTRY) * 100.0
        icon = "🟢" if pnl_eur >= 0 else "🔴"
        msg += f"💼 <b>TUA POSIZIONE: SHORT PUT {OPEN_PUT_STRIKE:.2f}€ @ {OPEN_PUT_ENTRY:.2f}€</b>\n"
        msg += f"• <b>Quota Ora:</b> <code>{curr_p:.2f} €</code> (B/A: {my_put['p_bid']:.2f}/{my_put['p_ask']:.2f})\n"
        msg += f"• <b>P&amp;L Aperto:</b> {icon} <b>{pnl_eur:+.2f} €</b> (<code>{pnl_pct:+.1f}%</code>) | <b>TP 50%:</b> <code>0.72 €</code>\n\n"

    msg += "🧱 <b>ARCHITETTURA ISTITUZIONALE</b>\n"
    msg += f"• <b>Max Pain:</b> <code>{max_pain_strike:.2f} €</code>\n"
    msg += f"• <b>Put Wall:</b> <code>{put_wall['strike']:.2f} €</code> ({put_wall['p_oi']:,} OI)\n"
    msg += f"• <b>Call Wall:</b> <code>{call_wall['strike']:.2f} €</code> ({call_wall['c_oi']:,} OI)\n"
    msg += f"• <b>OI Totale:</b> C <code>{tot_c_oi:,}</code> | P <code>{tot_p_oi:,}</code> (P/C: <code>{pc_oi:.2f}</code>)\n"
    msg += f"• <b>Volumi Oggi:</b> C <code>{tot_c_vol:,}</code> | P <code>{tot_p_vol:,}</code>\n"
    msg += f"• <b>Expected Move (1 SD):</b> <code>±{exp_move:.2f} €</code> (<code>{spot - exp_move:.2f}€</code> ↔️ <code>{spot + exp_move:.2f}€</code>)\n\n"

    msg += "🎯 <b>SCANNER PUT E ROLL</b>\n"
    for r in valid_chain:
        k = r["strike"]
        if not ((spot - 6.5) <= k <= (spot + 1.5)):
            continue
        prem = r["p_last"] or r["p_settle"]
        if prem <= 0.05:
            continue
        iv, delta, prob_prof = compute_put_greeks(spot, k, T, RISK_FREE_RATE, prem)
        be = k - prem
        ann_roc = ((prem / be) * 100.0) * (365.0 / dte)
        new_pmc = (PORTFOLIO_PMC + be) / 2.0
        tag = "📌" if abs(k - OPEN_PUT_STRIKE) < 0.01 else ("⭐" if -0.32 <= delta <= -0.20 else "🔹")
        msg += (
            f"{tag} <b>Put {k:.1f}€</b> | Quota: <code>{prem:.2f}€</code> (B/A: {r['p_bid']:.2f}/{r['p_ask']:.2f})\n"
            f"   Δ: <code>{delta:.2f}</code> | Prob: <code>{prob_prof:.0f}%</code> | BE: <code>{be:.2f}€</code> | "
            f"Ann: <b>{ann_roc:.1f}%</b> | PMC: <code>{new_pmc:.2f}€</code>\n"
        )

    return msg, spot, max_pain_strike


def main():
    # Ping immediato su Telegram per confermare che il job è attivo
    send_telegram_message("⏳ Avvio lettura catena opzioni Eurex BMW in corso...")

    target_url = "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222"
    dashboard_path = "eurex_dashboard.png"
    csv_path = "bmw_eurex_chain.csv"

    all_chains = []
    front_exp = "20/11/2026"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1500},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)

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

        try:
            page.wait_for_selector("table tr", timeout=15000)
            page.wait_for_timeout(2000)
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
                    page.locator(f"button:has-text('{exp_label}')").first.click(timeout=3000)
                    page.wait_for_timeout(2500)
                except Exception:
                    continue
            chain_part = scrape_expiration(page, exp_label)
            all_chains.extend(chain_part)

        browser.close()

    quant_report, spot, max_pain = build_quant_report(front_exp, all_chains)

    if generate_dashboard_chart(all_chains, front_exp, spot, max_pain, dashboard_path):
        send_telegram_photo(
            dashboard_path,
            f"📊 MAPPA OPEN INTEREST EUREX BMW ({front_exp})"
        )

    send_telegram_message(quant_report)

    if all_chains:
        save_enriched_csv(all_chains, csv_path, spot)
        send_telegram_document(
            csv_path,
            f"📂 bmw_eurex_chain.csv ({len(all_chains)} strike totali)"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_clean = traceback.format_exc()[-1200:].replace("<", "(").replace(">", ")").replace("&", "e")
        send_telegram_message(f"❌ ERRORE SCRIPT EUREX:\n{err_clean}")
        sys.exit(1)
