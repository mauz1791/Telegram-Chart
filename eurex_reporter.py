import os
import re
import sys
import csv
import math
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

# Posizione Put attualmente aperta in portafoglio
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


def parse_visible_table(page, exp_label: str) -> dict:
    """
    Unisce le 3 tabelle affiancate di Eurex (Call a sinistra, Strike al centro, Put a destra)
    accoppiando le celle che si trovano sulla stessa linea orizzontale (coordinata Y).
    """
    raw_lines = page.evaluate(r"""() => {
        const allCells = Array.from(document.querySelectorAll('table td, table th')).map(el => {
            const r = el.getBoundingClientRect();
            return {
                text: el.innerText.trim(),
                cx: r.left + r.width / 2,
                cy: r.top + r.height / 2,
                w: r.width,
                h: r.height
            };
        }).filter(c => c.w > 8 && c.h > 8 && c.text !== '');

        // Individua le celle Strike nella colonna centrale (formato XX.00 o XX.50 tra 25 e 120)
        // Per non confonderle con i prezzi, la colonna Strike è quella che ha almeno 5 numeri .00/.50 incolonnati alla stessa X
        const strikeCandidates = allCells.filter(c => /^\d{2,3}\.(00|50)$/.test(c.text));
        if (strikeCandidates.length === 0) return [];

        // Trova la coordinata X mediana della colonna Strike
        const xs = strikeCandidates.map(c => c.cx).sort((a, b) => a - b);
        const strikeColX = xs[Math.floor(xs.length / 2)];

        const strikeCells = strikeCandidates.filter(c => Math.abs(c.cx - strikeColX) < 35);

        const joinedRows = [];
        for (const sc of strikeCells) {
            const val = parseFloat(sc.text);
            if (isNaN(val) || val < 25 || val > 120) continue;

            // Prende tutte le celle numeriche o '-' sulla stessa riga orizzontale (stessa Y ± 12px)
            const rowCells = allCells.filter(c =>
                Math.abs(c.cy - sc.cy) < 12 &&
                /^(-|\d[\d,.]*)$/.test(c.text)
            );

            const leftCall = rowCells
                .filter(c => c.cx < sc.cx - 15)
                .sort((a, b) => a.cx - b.cx)
                .map(c => c.text);

            const rightPut = rowCells
                .filter(c => c.cx > sc.cx + 15)
                .sort((a, b) => a.cx - b.cx)
                .map(c => c.text);

            joinedRows.push({
                strike: val,
                callTokens: leftCall,
                putTokens: rightPut
            });
        }
        return joinedRows;
    }""")

    extracted = {}
    for item in raw_lines:
        k = float(item["strike"])
        c_tok = item["callTokens"]
        p_tok = item["putTokens"]

        # Le ultime 6 celle Call prima dello Strike sono: D.Settle, OI adj, Volume, Last, Bid, Ask
        c_6 = c_tok[-6:] if len(c_tok) >= 6 else (["0"] * (6 - len(c_tok)) + c_tok)
        # Le prime 6 celle Put dopo lo Strike sono: Bid, Ask, Last, Volume, OI adj, D.Settle
        p_6 = p_tok[:6] if len(p_tok) >= 6 else (p_tok + ["0"] * (6 - len(p_tok)))

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

    return extracted


def click_pagination_arrow(page, direction: str) -> bool:
    """
    Trova le frecce ^ (up) e v (down) incolonnate sopra e sotto la colonna centrale Strike
    cercando in TUTTE le tabelle della pagina.
    """
    target_coords = page.evaluate(r"""(dir) => {
        const allCells = Array.from(document.querySelectorAll('table td, table th')).map(el => {
            const r = el.getBoundingClientRect();
            return { text: el.innerText.trim(), cx: r.left + r.width / 2, cy: r.top + r.height / 2, w: r.width };
        }).filter(c => c.w > 8 && /^\d{2,3}\.(00|50)$/.test(c.text));

        if (allCells.length === 0) return null;
        const xs = allCells.map(c => c.cx).sort((a, b) => a - b);
        const strikeX = xs[Math.floor(xs.length / 2)];

        // Cerca tutte le icone SVG incolonnate con strikeX (entro 45px)
        const svgs = Array.from(document.querySelectorAll('svg')).map(s => {
            const r = s.getBoundingClientRect();
            return { el: s, x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
        }).filter(s => s.w > 5 && s.h > 5 && Math.abs(s.x - strikeX) < 45);

        if (svgs.length === 0) return null;
        svgs.sort((a, b) => a.y - b.y);

        const chosen = (dir === 'up') ? svgs[0] : svgs[svgs.length - 1];
        const clickable = chosen.el.closest('button, td, th, tr, div') || chosen.el;
        clickable.click();
        return { x: chosen.x, y: chosen.y };
    }""", direction)

    if not target_coords:
        return False

    try:
        page.mouse.click(target_coords["x"], target_coords["y"])
    except Exception:
        pass
    page.wait_for_timeout(900)
    return True


def screenshot_all_three_tables(page, path: str) -> bool:
    """Fotografa il riquadro unico che racchiude tutte e 3 le tabelle (Call + Strike + Put)."""
    clip_box = page.evaluate(r"""() => {
        const tables = Array.from(document.querySelectorAll('table')).filter(t => t.offsetWidth > 20 && t.offsetHeight > 50);
        if (tables.length === 0) return null;
        let minX = 99999, minY = 99999, maxX = 0, maxY = 0;
        for (const t of tables) {
            const r = t.getBoundingClientRect();
            minX = Math.min(minX, r.left + window.scrollX);
            minY = Math.min(minY, r.top + window.scrollY);
            maxX = Math.max(maxX, r.right + window.scrollX);
            maxY = Math.max(maxY, r.bottom + window.scrollY);
        }
        return { x: Math.max(0, minX - 10), y: Math.max(0, minY - 10), width: maxX - minX + 20, height: maxY - minY + 20 };
    }""")
    try:
        if clip_box and clip_box["width"] > 100 and clip_box["height"] > 100:
            page.screenshot(path=path, clip=clip_box, full_page=True)
            return True
    except Exception:
        pass
    return False


def scrape_full_expiration(page, exp_label: str, take_screenshots: bool = False):
    rows_data = {}
    screenshots = []

    rows_data.update(parse_visible_table(page, exp_label))

    # 1. Clicca la freccia SU (^) fino a raggiungere gli strike minimi (44€ - 53€)
    for _ in range(6):
        prev_min = min(rows_data.keys()) if rows_data else 999
        if not click_pagination_arrow(page, "up"):
            break
        rows_data.update(parse_visible_table(page, exp_label))
        new_min = min(rows_data.keys()) if rows_data else 999
        if new_min >= prev_min and len(rows_data) > 0:
            break

    # 2. Scorre dall'alto verso il basso raccogliendo tutti gli strike e gli screenshot
    for step_i in range(6):
        rows_data.update(parse_visible_table(page, exp_label))
        if take_screenshots:
            sp = f"chunk_{step_i}.png"
            if screenshot_all_three_tables(page, sp):
                screenshots.append(sp)

        prev_max = max(rows_data.keys()) if rows_data else 0
        if not click_pagination_arrow(page, "down"):
            break
        rows_data.update(parse_visible_table(page, exp_label))
        new_max = max(rows_data.keys()) if rows_data else 0
        if new_max <= prev_max:
            break

    print(f"[{exp_label}] Totale strike estratti con dati: {len(rows_data)}")
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
        page.goto(target_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)

   