import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

TZ_OFFSET = timedelta(hours=5, minutes=30)  # Asia/Colombo fixed offset
CEYPETCO_URL = "https://ceypetco.gov.lk/marketing-sales/"
CBSL_URL = "https://www.cbsl.gov.lk/en/rates-and-indicators/exchange-rates"

# GoldPriceZ requires an API key (keep it server-side only).
GOLDPRICEZ_URL = "https://goldpricez.com/api/rates"
GOLDPRICEZ_KEY = os.getenv("GOLDPRICEZ_KEY", "").strip()

OUT_PATH = os.path.join("public", "prices.json")


def now_colombo_iso() -> str:
    dt = datetime.now(timezone(TZ_OFFSET))
    return dt.isoformat(timespec="seconds")


def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "sl-market-friend-bot/1.0"})
    r.raise_for_status()
    return r.text


def parse_ceypetco_fuel(html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)

    def find_price_effect(product_name: str):
        pattern = re.compile(
            rf"{re.escape(product_name)}.*?Rs\.\s*([\d,]+\.\d+).*?Effect from:\s*([0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}})",
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(text)
        if not m:
            return None
        price = float(m.group(1).replace(",", ""))
        dd, mm, yyyy = m.group(2).split("-")
        eff_iso = f"{yyyy}-{mm}-{dd}"
        return price, eff_iso

    mapping = {
        "petrol_92": "Lanka Petrol 92 Octane",
        "petrol_95": "Lanka Petrol 95 Octane Euro 4",
        "diesel_auto": "Lanka Auto Diesel",
        "diesel_super": "Lanka Super Diesel 4 Star Euro 4",
        "kerosene": "Lanka Kerosene",
    }

    out = {}
    for key, name in mapping.items():
        res = find_price_effect(name)
        out[key] = {"price_lkr_per_l": None, "effective_from": None}
        if res:
            out[key]["price_lkr_per_l"], out[key]["effective_from"] = res
    return out


def parse_cbsl_fx(html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)

    def extract_triplet(currency_code: str):
        pattern = re.compile(
            rf"{currency_code}.*?Indicative\s*([\d.]+).*?Buy\s*([\d.]+).*?Sell\s*([\d.]+)",
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(text)
        if not m:
            return {"indicative": None, "buy": None, "sell": None}
        return {"indicative": float(m.group(1)), "buy": float(m.group(2)), "sell": float(m.group(3))}

    return {
        "usd_lkr_spot": extract_triplet("USD"),
        "gbp_lkr": extract_triplet("GBP"),
        "eur_lkr": extract_triplet("EUR"),
    }


def fetch_gold_lkr_per_gram():
    if not GOLDPRICEZ_KEY:
        return {"lkr_per_gram_24k": None, "lkr_per_gram_22k": None}

    params = {"api_key": GOLDPRICEZ_KEY, "metal": "gold", "currency": "LKR", "unit": "gram"}
    r = requests.get(GOLDPRICEZ_URL, params=params, timeout=30, headers={"User-Agent": "sl-market-friend-bot/1.0"})
    r.raise_for_status()
    data = r.json()

    rate = None
    if isinstance(data, dict):
        rate = data.get("rate") or data.get("price") or data.get("value")
    if rate is None:
        return {"lkr_per_gram_24k": None, "lkr_per_gram_22k": None}

    g24 = float(rate)
    g22 = g24 * (22.0 / 24.0)
    return {"lkr_per_gram_24k": round(g24, 2), "lkr_per_gram_22k": round(g22, 2)}


def main():
    ce_html = fetch_html(CEYPETCO_URL)
    cbsl_html = fetch_html(CBSL_URL)

    payload = {
        "app": "SL Market Friend",
        "tz": "Asia/Colombo",
        "lastUpdated": now_colombo_iso(),
        "sources": {"fuel": CEYPETCO_URL, "fx": CBSL_URL, "gold": "https://goldpricez.com/about/api"},
        "fuel": parse_ceypetco_fuel(ce_html),
        "fx": parse_cbsl_fx(cbsl_html),
        "gold": {**fetch_gold_lkr_per_gram(), "notes": "Indicative rates; jewellery shop rates may vary."},
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Updated {OUT_PATH} at {payload['lastUpdated']}")


if __name__ == "__main__":
    main()
