import os
import re
import sys
import csv
import math
import traceback
import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from scipy.stats import norm
from scipy.optimize import brentq
from playwright.sync_api import sync_playwright

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

raw_token = os.environ.get("TELEGRAM_TOKEN", "").strip()
if raw_token.lower().startswith("bot"):
    raw_token = raw_token[3:].strip()

TELEGRAM_TOKEN = raw_token
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
RISK_FREE_RATE = 0.025

# ==============================================================================
# CONFIGURAZIONE PORTAFOGLIO REALE (5 TITOLI XETRA + 9 OPZIONI EUREX)
# ==============================================================================
WATCHLIST = {
    "BMW": {
        "name": "BMW AG",
        "isin": "DE0005190003",
        "yf_ticker": "BMW.DE",
        "url": "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/BMW-950222",
        "shares": 100,
        "pmc": 74.00,
        "fair_value": 67.00,
        "open_options": [
            {"type": "P", "side": "SHORT", "strike": 57.00, "exp": "20/11/2026", "entry": 1.45},
            {"type": "C", "side": "LONG",  "strike": 74.00, "exp": "20/11/2026", "entry": 0.05},
            {"type": "C", "side": "LONG",  "strike": 100.00, "exp": "17/12/2027", "entry": 0.55},
        ]
    },
    "RWE": {
        "name": "RWE AG",
        "isin": "DE0007037129",
        "yf_ticker": "RWE.DE",
        "url": "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/RWE-948150",
        "shares": 100,
        "pmc": 60.00,
        "fair_value": 60.00,
        "open_options": [
            {"type": "C", "side": "SHORT", "strike": 60.00, "exp": "20/11/2026", "entry": 1.62},
            {"type": "P", "side": "SHORT", "strike": 55.00, "exp": "20/11/2026", "entry": 1.02},
        ]
    },
    "DTE": {
        "name": "Deutsche Telekom AG",
        "isin": "DE0005557508",
        "yf_ticker": "DTE.DE",
        "url": "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/Deutsche-Telekom-947538",
        "shares": 100,
        "pmc": 29.00,
        "fair_value": 31.00,
        "open_options": [
            {"type": "C", "side": "SHORT", "strike": 29.00, "exp": "20/11/2026", "entry": 0.04},
            {"type": "C", "side": "LONG",  "strike": 29.50, "exp": "20/11/2026", "entry": 0.06},
        ]
    },
    "DAI": {
        "name": "Mercedes-Benz Group AG",
        "isin": "DE0007100000",
        "yf_ticker": "MBG.DE",
        "url": "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/Mercedes-Benz-Group-2884628",
        "shares": 100,
        "pmc": 46.00,
        "fair_value": 52.00,
        "open_options": [
            {"type": "C", "side": "SHORT", "strike": 46.00, "exp": "20/11/2026", "entry": 0.05},
            {"type": "C", "side": "LONG",  "strike": 46.50, "exp": "20/11/2026", "entry": 0.06},
        ]
    },
    "DWS": {
        "name": "DWS Group GmbH",
        "isin": "DE000DWS1007",
        "yf_ticker": "DWS.DE",
        "url": "https://www.eurex.com/ex-en/markets/equ/equ-opt/options/DWS-Group-947324",
        "shares": 100,
        "pmc": 74.00,
        "fair_value": 70.00,
        "open_options": []
    },
}

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


def fetch_yf_technicals(yf_symbol: str) -> dict:
    """Scarica Prezzo Spot Xetra, RSI(14), Volatilità Storica HV20 e SMA50 da Yahoo Finance."""
    info = {"spot": 0.0, "rsi": 50.0, "hv20": 0.0, "sma50": 0.0}
    if not HAS_YF or not yf_symbol:
        return info
    try:
        df = yf.Ticker(yf_symbol).history(period="6mo")
        if df.empty or len(df) < 22:
            return info
        closes = df["Close"].dropna()
        info["spot"] = float(closes.iloc[-1])
        info["sma50"] = float(closes.tail(50).mean())

        # Calcolo RSI(14)
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_s = 100.0 - (100.0 / (1.0 + rs))
        if not np.isnan(rsi_s.iloc[-1]):
            info["rsi"] = float(rsi_s.iloc[-1])

        # Calcolo Volatilità Storica a 20 giorni annualizzata (HV20)
        log_ret = np.log(closes / closes.shift(1)).dropna()
        info["hv20"] = float(log_ret.tail(20).std() * math.sqrt(252) * 100.0)
    except Exception:
        pass
    return info


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


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


def compute_call_greeks(S: float, K: float, T: float, r: float, price: float):
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if price <= intrinsic + 1e-4 or T <= 0:
        return 0.0, 0.0, 0.0
    try:
        iv = brentq(lambda x: bs_call_price(S, K, T, r, x) - price, 0.01, 3.0)
    except Exception:
        return 0.0, 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    delta = norm.cdf(d1)
    be = K + price
    d2_be = (math.log(S / be) + (r - 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    prob_profit = (1.0 - norm.cdf(d2_be)) * 100.0
    return iv * 100.0, delta, prob_profit


def dismiss_cookies(page):
    try:
        cookie_btn = page.locator(
            "button:has-text('DECLINE ALL'), button:has-text('ACCEPT ALL'), "
            "button:has-text('Accept'), button:has-text('Decline')"
        )
        if cookie_btn.count() > 0:
            cookie_btn.first.click(timeout=2000)
            page.wait_for_timeout(800)
    except Exception:
        pass


def open_eurex_option_page(page, ticker: str, cfg: dict) -> bool:
    """
    Apre l'URL diretto di Eurex. Se l'ID numerico dell'URL dovesse essere cambiato
    (nessuna tabella presente), cerca automaticamente il prodotto su Eurex tramite ISIN.
    """
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    dismiss_cookies(page)

    has_table = page.locator("table tr").count() > 5
    if has_table:
        return True

    # Auto-Discovery tramite ISIN nella ricerca di Eurex
    isin = cfg.get("isin", "")
    try:
        search_url = f"https://www.eurex.com/ex-en/find?q={isin}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(3500)
        dismiss_cookies(page)

        opt_link = page.locator("a[href*='/markets/equ/equ-opt/options/']").first
        if opt_link.count() > 0:
            href = opt_link.get_attribute("href")
            if href:
                full_url = href if href.startswith("http") else f"https://www.eurex.com{href}"
                cfg["url"] = full_url
                page.goto(full_url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(4000)
                return page.locator("table tr").count() > 5
    except Exception:
        pass

    return False


def extract_current_view(page, ticker: str, exp_label: str) -> dict:
    raw = page.evaluate(r"""() => {
        const trs = Array.from(document.querySelectorAll('tr')).map(tr =>
            Array.from(tr.querySelectorAll('td, th')).map(c => c.innerText.trim())
        );

        const strikes = [];
        const elevenColRows = [];

        for (const r of trs) {
            const nonEmpty = r.filter(x => x !== '');
            if (nonEmpty.length === 1 && /^\d{1,3}\.\d{2}$/.test(nonEmpty[0])) {
                const v = parseFloat(nonEmpty[0]);
                if (v >= 2.0 && v <= 300.0) strikes.push(v);
                continue;
            }
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

    call_rows = data_rows[:n] if len(data_rows) >= n else []
    put_rows = data_rows[n:2 * n] if len(data_rows) >= 2 * n else []

    for i, k in enumerate(strikes):
        c_r = call_rows[i] if i < len(call_rows) else []
        p_r = put_rows[i] if i < len(put_rows) else []

        c_end = len(c_r) - 1 if (c_r and c_r[-1] == "") else len(c_r)
        p_start = 1 if (p_r and p_r[0] == "") else 0

        def get_c(idx):
            return parse_num(c_r[idx]) if 0 <= idx < len(c_r) else 0.0

        def get_p(idx):
            return parse_num(p_r[idx]) if 0 <= idx < len(p_r) else 0.0

        extracted[k] = {
            "ticker": ticker,
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
    coords = page.evaluate(r"""(dir) => {
        const strikeCells = Array.from(document.querySelectorAll('td, th')).filter(el => {
            const t = el.innerText.trim();
            return /^\d{1,3}\.\d{2}$/.test(t) && parseFloat(t) >= 2.0 && el.offsetWidth > 10;
        });
        if (strikeCells.length < 5) return null;
        const targetCell = (dir === 'up') ? strikeCells[0] : strikeCells[strikeCells.length - 1];
        targetCell.scrollIntoView({ block: 'center' });
        const r = targetCell.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: (dir === 'up') ? (r.top - 18) : (r.bottom + 18) };
    }""", direction)

    if not coords:
        return False
    try:
        page.mouse.click(coords["x"], coords["y"])
        page.wait_for_timeout(850)
        return True
    except Exception:
        return False


def scrape_expiration(page, ticker: str, exp_label: str):
    rows_data = {}
    rows_data.update(extract_current_view(page, ticker, exp_label))

    for _ in range(3):
        prev_min = min(rows_data.keys()) if rows_data else 999
        if not click_arrow(page, "up"):
            break
        rows_data.update(extract_current_view(page, ticker, exp_label))
        if (min(rows_data.keys()) if rows_data else 999) >= prev_min:
            break

    for _ in range(4):
        prev_max = max(rows_data.keys()) if rows_data else 0
        if not click_arrow(page, "down"):
            break
        rows_data.update(extract_current_view(page, ticker, exp_label))
        if (max(rows_data.keys()) if rows_data else 0) <= prev_max:
            break

    return sorted(rows_data.values(), key=lambda x: x["strike"])


def generate_dashboard_chart(chain: list, ticker: str, exp_date_str: str, spot: float, max_pain: float, out_path: str):
    valid = [r for r in chain if r["ticker"] == ticker and r["expiration"] == exp_date_str]
    if not valid:
        return False

    strikes = [r["strike"] for r in valid]
    c_oi = [r["c_oi"] for r in valid]
    p_oi = [r["p_oi"] for r in valid]

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 5.5), dpi=150)
    x = np.arange(len(strikes))
    width = 0.38

    ax.bar(x - width / 2, c_oi, width, label="Call OI (Resistenze)", color="#00d2ff", alpha=0.85)
    ax.bar(x + width / 2, p_oi, width, label="Put OI (Supporti)", color="#ff4b4b", alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{s:.0f}€" if s.is_integer() else f"{s:.2f}€" for s in strikes], rotation=45, fontsize=9)
    ax.set_ylabel("Open Interest (Contratti)", fontsize=10)
    ax.set_title(
        f"{ticker} Eurex Options ({exp_date_str}) | Spot: {spot:.2f}€ | Max Pain: {max_pain:.2f}€",
        fontsize=12, fontweight="bold", pad=15
    )
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.legend(loc="upper right", fontsize=10)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)
    return True


def enrich_and_build_report(ticker: str, cfg: dict, exp_date_str: str, chain: list, tech: dict, alerts_out: list):
    valid_chain = [r for r in chain if r["ticker"] == ticker and r["expiration"] == exp_date_str]
    today = datetime.utcnow()
    dte = 20
    try:
        exp_dt = datetime.strptime(exp_date_str, "%d/%m/%Y")
        dte = max(1, (exp_dt - today).days)
    except Exception:
        pass
    T = dte / 365.0

    # Stima Spot da Put-Call Parity o da Yahoo Finance Xetra
    spot_estimates = []
    for row in valid_chain:
        c_p = row["c_settle"] or row["c_last"]
        p_p = row["p_settle"] or row["p_last"]
        if c_p > 0.05 and p_p > 0.05:
            spot_estimates.append(row["strike"] + c_p - p_p)

    if spot_estimates:
        spot = float(np.median(spot_estimates))
    elif tech["spot"] > 0:
        spot = tech["spot"]
    else:
        spot = cfg["pmc"]

    pmc = cfg["pmc"]
    fv = cfg["fair_value"]
    stock_pl = (spot - pmc) * cfg["shares"]
    stock_pl_pct = ((spot - pmc) / pmc) * 100.0

    if not valid_chain:
        msg = (
            f"📊 <b>REPORT {ticker} ({cfg['name']})</b>\n"
            f"💶 <b>Spot Xetra:</b> <code>{spot:.2f} €</code> | <b>PMC:</b> <code>{pmc:.2f} €</code> (<code>{stock_pl:+.0f}€ / {stock_pl_pct:+.1f}%</code>)\n"
            f"📈 <b>RSI(14):</b> <code>{tech['rsi']:.1f}</code> | <b>HV20:</b> <code>{tech['hv20']:.1f}%</code>\n"
            f"⚠️ Catena Eurex non rilevata su URL: <code>{cfg['url']}</code>"
        )
        return msg, spot, spot, stock_pl, 0.0

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
        iv_atm = tech["hv20"] if tech["hv20"] > 0 else 30.0
    exp_move = spot * (iv_atm / 100.0) * math.sqrt(T)

    # Edge Volatilità (IV vs HV20)
    hv20 = tech["hv20"]
    vol_edge_str = f"IV <code>{iv_atm:.1f}%</code> vs HV20 <code>{hv20:.1f}%</code>"
    if hv20 > 0 and iv_atm > hv20 * 1.10:
        vol_edge_str += " (🟢 Premi Ricchi)"
    elif hv20 > 0:
        vol_edge_str += " (⚪ Premi Normali)"

    msg = f"📊 <b>QUANT REPORT EUREX — {ticker} ({cfg['name']})</b>\n"
    msg += f"📅 <b>Scadenza:</b> <code>{exp_date_str}</code> ({dte} gg) | <b>Strike Lette:</b> <code>{len(valid_chain)}</code>\n"
    msg += f"💶 <b>Spot:</b> <code>{spot:.2f} €</code> | <b>PMC:</b> <code>{pmc:.2f} €</code> (<code>{stock_pl:+.0f}€ / {stock_pl_pct:+.1f}%</code>) | <b>FV:</b> <code>{fv:.0f} €</code>\n"
    msg += f"📈 <b>RSI(14):</b> <code>{tech['rsi']:.1f}</code> | <b>SMA50:</b> <code>{tech['sma50']:.2f}€</code> | {vol_edge_str}\n\n"

    # Sezione 1: Monitoraggio Posizioni Opzioni Aperte (LONG e SHORT)
    open_opts = cfg.get("open_options", [])
    ticker_opt_pl = 0.0
    if open_opts:
        msg += f"💼 <b>POSIZIONI OPZIONI APERTE SU {ticker}:</b>\n"
        for pos in open_opts:
            match = next((r for r in chain if r["ticker"] == ticker and r["expiration"] == pos["exp"] and abs(r["strike"] - pos["strike"]) < 0.01), None)
            curr_val = pos["entry"]
            bid_v, ask_v = 0.0, 0.0
            if match:
                if pos["type"] == "P":
                    curr_val = match["p_last"] or match["p_settle"] or match["p_ask"] or pos["entry"]
                    bid_v, ask_v = match["p_bid"], match["p_ask"]
                else:
                    curr_val = match["c_last"] or match["c_settle"] or match["c_ask"] or pos["entry"]
                    bid_v, ask_v = match["c_bid"], match["c_ask"]

            if pos["side"] == "SHORT":
                pnl_eur = (pos["entry"] - curr_val) * 100.0
                pnl_pct = ((pos["entry"] - curr_val) / pos["entry"]) * 100.0
                lbl = f"SHORT {'PUT' if pos['type'] == 'P' else 'CALL'}"
                tp_str = f" | TP50%: <code>{pos['entry'] * 0.50:.2f}€</code>"
                if pnl_pct >= 50.0:
                    alerts_out.append(f"🟢 <b>{ticker}</b>: {lbl} {pos['strike']:.1f}€ a +{pnl_pct:.0f}% (+{pnl_eur:.0f}€) -> Pronta per Take Profit!")
                elif pos["type"] == "P" and spot < pos["strike"]:
                    alerts_out.append(f"🟡 <b>{ticker}</b>: {lbl} {pos['strike']:.1f}€ è ITM (Spot {spot:.2f}€) -> Monitorare per eventuale Roll.")
            else:
                pnl_eur = (curr_val - pos["entry"]) * 100.0
                pnl_pct = ((curr_val - pos["entry"]) / pos["entry"]) * 100.0
                lbl = f"LONG {'PUT' if pos['type'] == 'P' else 'CALL'}"
                tp_str = ""

            ticker_opt_pl += pnl_eur
            icon = "🟢" if pnl_eur >= 0 else "🔴"
            msg += (
                f"• <b>{lbl} {pos['strike']:.2f}€</b> ({pos['exp']}) @ <code>{pos['entry']:.2f}€</code>\n"
                f"  Quota: <code>{curr_val:.2f}€</code> (B/A: {bid_v:.2f}/{ask_v:.2f}) | "
                f"P&amp;L: {icon} <b>{pnl_eur:+.1f}€</b> (<code>{pnl_pct:+.1f}%</code>){tp_str}\n"
            )
        msg += "\n"

    # Segnale opportunità per titoli senza vendita attiva o in ipervenduto
    if not any(o["side"] == "SHORT" for o in open_opts):
        alerts_out.append(f"🔵 <b>{ticker}</b>: Nessuna opzione Short attiva (RSI {tech['rsi']:.0f}) -> Valuta ingresso Ruota.")

    # Sezione 2: Muri Istituzionali
    msg += "🧱 <b>ARCHITETTURA ISTITUZIONALE</b>\n"
    msg += f"• <b>Max Pain:</b> <code>{max_pain_strike:.2f} €</code>\n"
    msg += f"• <b>Put Wall:</b> <code>{put_wall['strike']:.2f} €</code> ({put_wall['p_oi']:,} OI)\n"
    msg += f"• <b>Call Wall:</b> <code>{call_wall['strike']:.2f} €</code> ({call_wall['c_oi']:,} OI)\n"
    msg += f"• <b>OI Totale:</b> C <code>{tot_c_oi:,}</code> | P <code>{tot_p_oi:,}</code> (P/C: <code>{pc_oi:.2f}</code>)\n"
    msg += f"• <b>Volumi Oggi:</b> C <code>{tot_c_vol:,}</code> | P <code>{tot_p_vol:,}</code>\n"
    msg += f"• <b>Expected Move (1 SD):</b> <code>±{exp_move:.2f} €</code> (<code>{spot - exp_move:.2f}€</code> ↔️ <code>{spot + exp_move:.2f}€</code>)\n\n"

    # Sezione 3: Scanner Cash-Secured Put
    msg += "🎯 <b>LATO PUT (Vendita Put / Mediazione PMC)</b>\n"
    for r in valid_chain:
        k = r["strike"]
        if not ((spot * 0.88) <= k <= (spot * 1.01)):
            continue
        prem = r["p_last"] or r["p_settle"]
        if prem <= 0.08:
            continue
        iv, delta, prob_prof = compute_put_greeks(spot, k, T, RISK_FREE_RATE, prem)
        be = k - prem
        ann_roc = ((prem / be) * 100.0) * (365.0 / dte)
        new_pmc = (pmc + be) / 2.0
        tag = "⭐" if -0.32 <= delta <= -0.20 else "🔹"
        msg += (
            f"{tag} <b>Put {k:.1f}€</b> | Quota: <code>{prem:.2f}€</code> (B/A: {r['p_bid']:.2f}/{r['p_ask']:.2f})\n"
            f"   Δ: <code>{delta:.2f}</code> | Prob: <code>{prob_prof:.0f}%</code> | BE: <code>{be:.2f}€</code> | "
            f"Ann: <b>{ann_roc:.1f}%</b> | PMC 200az: <code>{new_pmc:.2f}€</code>\n"
        )

    # Sezione 4: Scanner Covered Call (solo premi >= 0.20 € per evitare micro-premi inefficienti)
    msg += "\n🛡️ <b>LATO CALL (Vendita Covered Call >= 0.20€)</b>\n"
    found_call = False
    for r in valid_chain:
        k = r["strike"]
        if not ((spot * 1.01) <= k <= (spot * 1.16)):
            continue
        prem = r["c_last"] or r["c_settle"]
        if prem < 0.20:
            continue
        found_call = True
        iv_c, delta_c, prob_c = compute_call_greeks(spot, k, T, RISK_FREE_RATE, prem)
        ann_roc_c = ((prem / spot) * 100.0) * (365.0 / dte)
        tag_c = "⭐" if (0.18 <= delta_c <= 0.35 and k >= pmc * 0.95) else "🔸"
        msg += (
            f"{tag_c} <b>Call {k:.1f}€</b> | Quota: <code>{prem:.2f}€</code> (B/A: {r['c_bid']:.2f}/{r['c_ask']:.2f})\n"
            f"   Δ: <code>+{delta_c:.2f}</code> | Prob OTM: <code>{prob_c:.0f}%</code> | "
            f"Yield Annuo: <b>{ann_roc_c:.1f}%</b> | OI: <code>{r['c_oi']:,}</code>\n"
        )
    if not found_call:
        msg += "<i>Nessuna Call OTM con premio >= 0.20€ (attendi rimbalzo del sottostante).</i>\n"

    return msg, spot, max_pain_strike, stock_pl, ticker_opt_pl


def save_multi_ticker_csv(all_chains: list, spots_by_ticker: dict, filepath: str):
    if not all_chains:
        return
    fieldnames = [
        "ticker", "expiration", "dte", "spot_implied", "strike",
        "c_settle", "c_bid", "c_ask", "c_last", "c_vol", "c_oi",
        "p_bid", "p_ask", "p_last", "p_settle", "p_vol", "p_oi",
        "put_iv_pct", "put_delta", "put_prob_profit_pct", "put_breakeven", "put_roc_ann_pct",
        "call_iv_pct", "call_delta", "call_roc_ann_pct"
    ]
    today = datetime.utcnow()
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_chains:
            tkr = r["ticker"]
            spot = spots_by_ticker.get(tkr, 50.0)
            dte = 20
            try:
                exp_dt = datetime.strptime(r["expiration"], "%d/%m/%Y")
                dte = max(1, (exp_dt - today).days)
            except Exception:
                pass
            T = dte / 365.0

            p_prem = r["p_settle"] or r["p_last"]
            p_iv, p_delta, p_prob = compute_put_greeks(spot, r["strike"], T, RISK_FREE_RATE, p_prem)
            p_be = (r["strike"] - p_prem) if p_prem > 0 else r["strike"]
            p_roc = (((p_prem / p_be) * 100.0) * (365.0 / dte)) if (p_prem > 0 and p_be > 0) else 0.0

            c_prem = r["c_settle"] or r["c_last"]
            c_iv, c_delta, _ = compute_call_greeks(spot, r["strike"], T, RISK_FREE_RATE, c_prem)
            c_roc = (((c_prem / spot) * 100.0) * (365.0 / dte)) if (c_prem > 0 and spot > 0) else 0.0

            row_out = dict(r)
            row_out.update({
                "dte": dte,
                "spot_implied": round(spot, 2),
                "put_iv_pct": round(p_iv, 2),
                "put_delta": round(p_delta, 3),
                "put_prob_profit_pct": round(p_prob, 1),
                "put_breakeven": round(p_be, 2),
                "put_roc_ann_pct": round(p_roc, 2),
                "call_iv_pct": round(c_iv, 2),
                "call_delta": round(c_delta, 3),
                "call_roc_ann_pct": round(c_roc, 2),
            })
            writer.writerow(row_out)


def main():
    tickers_list = ", ".join(WATCHLIST.keys())
    send_telegram_message(f"⏳ <b>Centro di Controllo Wheel Strategy</b>\nAvvio scansione completa per 5 titoli: <code>{tickers_list}</code>...")

    all_chains = []
    spots_by_ticker = {}
    alerts = []
    total_stock_pl = 0.0
    total_opt_pl = 0.0
    total_short_put_margin = 0.0
    csv_path = "eurex_wheel_chains.csv"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1500},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for ticker, cfg in WATCHLIST.items():
            for o in cfg.get("open_options", []):
                if o["type"] == "P" and o["side"] == "SHORT":
                    total_short_put_margin += o["strike"] * 100.0

            tech = fetch_yf_technicals(cfg.get("yf_ticker", ""))

            try:
                ok = open_eurex_option_page(page, ticker, cfg)
                exp_dates = []
                if ok:
                    for b_text in page.locator("button").all_inner_texts():
                        m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", b_text)
                        if m and m.group(1) not in exp_dates:
                            exp_dates.append(m.group(1))

                front_exp = exp_dates[0] if exp_dates else "20/11/2026"

                if ok:
                    for idx, exp_label in enumerate(exp_dates[:2]):
                        if idx > 0:
                            try:
                                page.locator(f"button:has-text('{exp_label}')").first.click(timeout=3000)
                                page.wait_for_timeout(2200)
                            except Exception:
                                continue
                        chain_part = scrape_expiration(page, ticker, exp_label)
                        all_chains.extend(chain_part)

                report_msg, spot, max_pain, s_pl, o_pl = enrich_and_build_report(
                    ticker, cfg, front_exp, all_chains, tech, alerts
                )
                spots_by_ticker[ticker] = spot
                total_stock_pl += s_pl
                total_opt_pl += o_pl

                chart_path = f"dashboard_{ticker}.png"
                if generate_dashboard_chart(all_chains, ticker, front_exp, spot, max_pain, chart_path):
                    send_telegram_photo(chart_path, f"📊 MAPPA OPEN INTEREST — {ticker} ({front_exp})")

                send_telegram_message(report_msg)

            except Exception as e:
                send_telegram_message(f"⚠️ Errore su {ticker}: {str(e)[:200]}")

        browser.close()

    # Riepilogo Esecutivo Globale (Centro di Controllo Portafoglio)
    summary_msg = "🏛️ <b>RIEPILOGO ESECUTIVO PORTAFOGLIO (WHEEL CONTROL CENTER)</b>\n\n"
    summary_msg += f"• <b>P&amp;L Irrealizzato Azioni (5 Lotti):</b> <code>{total_stock_pl:+.2f} €</code>\n"
    summary_msg += f"• <b>P&amp;L Aperto Opzioni Eurex:</b> <code>{total_opt_pl:+.2f} €</code>\n"
    summary_msg += f"• <b>Margine Cash-Secured Put Impegnato:</b> <code>{total_short_put_margin:,.0f} €</code>\n\n"
    summary_msg += "🚦 <b>SEMAFORO OPERATIVO DI OGGI:</b>\n"
    if alerts:
        summary_msg += "\n".join(alerts)
    else:
        summary_msg += "• Tutte le posizioni procedono regolarmente (Theta decay attivo)."

    send_telegram_message(summary_msg)

    if all_chains:
        save_multi_ticker_csv(all_chains, spots_by_ticker, csv_path)
        send_telegram_document(
            csv_path,
            f"📂 eurex_wheel_chains.csv ({len(all_chains)} strike totali su {len(WATCHLIST)} titoli)"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_clean = traceback.format_exc()[-1200:].replace("<", "(").replace(">", ")").replace("&", "e")
        send_telegram_message(f"❌ ERRORE CRITICO SCRIPT:\n{err_clean}")
        sys.exit(1)
