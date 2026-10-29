import os
import re
import sys
import csv
import math
import traceback
import requests
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
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

# Posizione Put aperta in portafoglio
OPEN_PUT_EXP = "20/11/2026"
OPEN_PUT_STRIKE = 57.00
OPEN_PUT_ENTRY = 1.45

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


def parse_three_eurex_tables(page, exp_label: str) -> dict:
    """
    Legge le 3 tabelle separate di Eurex (Call Table, Strike Table, Put Table)
    e le unisce indice per indice (riga 0..14).
    """
    data = page.evaluate(r"""() => {
        const tables = Array.from(document.querySelectorAll('table'));
        let strikeList = [];
        let dataTables = [];

        for (const t of tables) {
            const rows = Array.from(t.querySelectorAll('tbody tr, tr')).map(tr =>
                Array.from(tr.querySelectorAll('td, th'))
                    .map(c => c.innerText.trim())
                    .filter(txt => txt !== '')
            ).filter(r => r.length > 0);

            // Controlla se è la tabella centrale degli Strike (righe da 1 sola cella XX.00 / XX.50)
            const singleStrikeRows = rows.filter(r => r.length === 1 && /^\d{2,3}\.(00|50)$/.test(r[0]));
            if (singleStrikeRows.length >= 5) {
                strikeList = singleStrikeRows.map(r => parseFloat(r[0]));
                continue;
            }

            // Controlla se è una tabella dati Call o Put (righe con almeno 6 valori numerici o '-')
            const validDataRows = rows.filter(r =>
                r.length >= 6 &&
                !r.some(cell => /strike|settle|volume|total/i.test(cell)) &&
                r.filter(cell => /^(-|\d[\d,.]*)$/.test(cell)).length >= 6
            );
            if (validDataRows.length >= 5) {
                dataTables.push(validDataRows);
            }
        }

        // Caso fallback: se fosse una singola tabella unificata
        if (strikeList.length === 0) {
            const allTrs = Array.from(document.querySelectorAll('tr')).map(tr =>
                Array.from(tr.querySelectorAll('td, th')).map(c => c.innerText.trim()).filter(x => x !== '')
            ).filter(r => r.length >= 13);
            return { mode: 'single', rows: allTrs };
        }

        return {
            mode: 'three_tables',
            strikes: strikeList,
            callRows: dataTables.length >= 1 ? dataTables[0] : [],
            putRows: dataTables.length >= 2 ? dataTables[1] : []
        };
    }""")

    extracted = {}

    if data.get("mode") == "three_tables":
        strikes = data.get("strikes", [])
        call_rows = data.get("callRows", [])
        put_rows = data.get("putRows", [])
        n = min(len(strikes), len(call_rows), len(put_rows))

        for i in range(n):
            k = float(strikes[i])
            if not (25.0 <= k <= 120.0):
                continue
            c_r = call_rows[i]
            p_r = put_rows[i]

            # Nella tabella Call (a sinistra), le ultime 6 colonne sono: D.Settle, OI adj, Volume, Last, Bid, Ask
            c_6 = c_r[-6:] if len(c_r) >= 6 else (["0"] * (6 - len(c_r)) + c_r)
            # Nella tabella Put (a destra), le prime 6 colonne sono: Bid, Ask, Last, Volume, OI adj, D.Settle
            p_6 = p_r[:6] if len(p_r) >= 6 else (p_r + ["0"] * (6 - len(p_r)))

            extracted[k] = {
                "expiration": exp_label,
                "strike": k,
                "c_settle": parse_num(c_6[0]),
                "c_oi": int(parse_num(c_6[1])),
                "c_vol": int(parse_num(c_6[2])),
                "c_last": parse_num(c_6[3]),
                "c_bid": parse_num(c_6[4]),
                "c_ask": parse_num(c_6[5]),
                "p_bid": parse_num(p_6[0]),
                "p_ask": parse_num(p_6[1]),
                "p_last": parse_num(p_6[2]),
                "p_vol": int(parse_num(p_6[3])),
                "p_oi": int(parse_num(p_6[4])),
                "p_settle": parse_num(p_6[5]),
            }

    elif data.get("mode") == "single":
        for r in data.get("rows", []):
            mid = len(r) // 2
            k = parse_num(r[mid])
            if 25.0 <= k <= 120.0:
                extracted[k] = {
                    "expiration": exp_label,
                    "strike": k,
                    "c_settle": parse_num(r[mid - 6]),
                    "c_oi": int(parse_num(r[mid - 5])),
                    "c_vol": int(parse_num(r[mid - 4])),
                    "c_last": parse_num(r[mid - 3]),
                    "c_bid": parse_num(r[mid - 2]),
                    "c_ask": parse_num(r[mid - 1]),
                    "p_bid": parse_num(r[mid + 1]),
                    "p_ask": parse_num(r[mid + 2]),
                    "p_last": parse_num(r[mid + 3]),
                    "p_vol": int(parse_num(r[mid + 4])),
                    "p_oi": int(parse_num(r[mid + 5])),
                    "p_settle": parse_num(r[mid + 6]),
                }

    return extracted


def click_strike_table_arrow(page, direction: str) -> bool:
    """
    Cerca le frecce ^ (up) e v (down) specificamente dentro la tabella centrale degli Strike.
    """
    clicked = page.evaluate(r"""(dir) => {
        const tables = Array.from(document.querySelectorAll('table'));
        let strikeTable = null;
        for (const t of tables) {
            if (t.innerText.includes('Strike')) {
                strikeTable = t;
                break;
            }
        }
        const scope = strikeTable || document;
        const svgs = Array.from(scope.querySelectorAll('tbody svg, tr svg'));
        if (svgs.length === 0) return false;

        const targetSvg = (dir === 'up') ? svgs[0] : svgs[svgs.length - 1];
        const btn = targetSvg.closest('button, td, th, tr, div') || targetSvg;
        btn.scrollIntoView({ block: 'center' });
        if (typeof btn.click === 'function') {
            btn.click();
        } else {
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        }
        return true;
    }""", direction)
    if clicked:
        page.wait_for_timeout(900)
    return bool(clicked)


def scrape_full_expiration(page, exp_label: str, take_screenshots: bool = False):
    rows_data = {}
    screenshots = []

    rows_data.update(parse_three_eurex_tables(page, exp_label))

    # 1. Clicca la freccia SU (^) fino agli strike minimi (44€ - 53€)
    for _ in range(5):
        prev_min = min(rows_data.keys()) if rows_data else 999
        if not click_strike_table_arrow(page, "up"):
            break
        rows_data.update(parse_three_eurex_tables(page, exp_label))
        new_min = min(rows_data.keys()) if rows_data else 999
        if new_min >= prev_min and len(rows_data) > 0:
            break

    # 2. Scorre verso il basso catturando ogni blocco
    for step_i in range(6):
        rows_data.update(parse_three_eurex_tables(page, exp_label))
        if take_screenshots:
            sp = f"chunk_{step_i}.png"
            try:
                page.screenshot(path=sp, full_page=False)
                screenshots.append(sp)
            except Exception:
                pass

        prev_max = max(rows_data.keys()) if rows_data else 0
        if not click_strike_table_arrow(page, "down"):
            break
        rows_data.update(parse_three_eurex_tables(page, exp_label))
        new_max = max(rows_data.keys()) if rows_data else 0
        if new_max <= prev_max:
            break

    print(f"[{exp_label}] Strike estratti: {len(rows_data)}")
    return sorted(rows_data.values(), key=lambda x: x["strike"]), screenshots


def stitch_images_vertically(image_paths: list, output_path: str):
    valid_imgs = [Image.open(p) for p in image_paths if os.path.exists(p)]
    if not valid_imgs:
        return False
    unique_imgs = [valid_imgs[0]]
    for im in valid_imgs[1:]:
        if list(im.getdata()) != list(unique_imgs[-1].getdata()):
            unique_imgs.append(im)

    widths, heights = zip(*(i.size for i in unique_imgs))
    stitched = Image.new("RGB", (max(widths), sum(heights)), (255, 255, 255))
    y_offset = 0
    for im in unique_imgs:
        stitched.paste(im, (0, y_offset))
        y_offset += im.size[1]
    stitched.save(output_path)
    return True


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

    ax.bar(x - width / 2, c_oi, width, label="Call Open Interest (Resistenze)", color="#00d2ff", alpha=0.85)
    ax.bar(x + width / 2, p_oi, width, label="Put Open Interest (Supporti)", color="#ff4b4b", alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{s:.0f}€" if s.is_integer() else f"{s:.1f}€" for s in strikes], rotation=45, fontsize=9)
    ax.set_ylabel("Contratti Aperti (Open Interest)", fontsize=10)
    ax.set_title(
        f"BMW Eurex Options — Mappa Open Interest ({exp_date_str}) | Spot: {spot:.2f}€ | Max Pain: {max_pain:.1f}€",
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
        return "⚠️ Nessuna riga estratta.", 58.00, 57.0

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
    spot = float(np.median(spot_estimates)) if spot_estimates else 58.00

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
        msg += f"• <b>P&L Aperto:</b> {icon} <b>{pnl_eur:+.2f} €</b> (<code>{pnl_pct:+.1f}%</code>) | <b>TP 50%:</b> <code>0.72 €</code>\n\n"

    msg += "🧱 <b>ARCHITETTURA ISTITUZIONALE</b>\n"
    msg += f"• <b>Max Pain:</b> <code>{max_pain_strike:.2f} €</code>\n"
    msg += f"• <b>Put Wall:</b> <code>{put_wall['strike']:.2f} €</code> ({put_wall['p_oi']:,} OI)\n"
    msg += f"• <b>Call Wall:</b> <code>{call_wall['strike']:.2f} €</code> ({call_wall['c_oi']:,} OI)\n"
    msg += f"• <b>OI Totale:</b> C <code>{tot_c_oi:,}</code> | P <code>{tot_p_oi:,}</code> (P/C: <code>{pc_oi:.2f}</code>)\n"
    msg += f"• <b>Volumi Oggi:</b> C <code>{tot_c_vol:,}</code> | P <code>{tot_p_vol:,}</code>\n"
    msg += f"• <b>Expected Move (1 SD):</b> <code>±{exp_move:.2f} €</code> (<code>{spot - exp_move:.2f}€</code> ↔️ <code>{spot + exp_move:.2f}€</code>)\n\n"

    msg += "🎯 <b>SCANNER PUT & ROLL</b>\n"
    for r in valid_chain:
        k = r["strike"]
        if not ((spot - 6.5) <= k <= (spot + 1.0)):
            continue
        prem = r["p_last"] or r["p_settle"]
        if prem <= 0.10:
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
    target_url = "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222"
    stitched_path = "eurex_full_chain.png"
    dashboard_path = "eurex_dashboard.png"
    csv_path = "bmw_eurex_chain.csv"

    all_chains = []
    front_exp = "20/11/2026"
    chunk_files = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1500},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        # Usa domcontentloaded per evitare timeout di rete su Eurex
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
            p