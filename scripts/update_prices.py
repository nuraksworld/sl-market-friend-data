import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# -----------------------
# Config
# -----------------------
TZ_OFFSET = timedelta(hours=5, minutes=30)  # Asia/Colombo fixed offset

CEYPETCO_URL = "https://ceypetco.gov.lk/marketing-sales/"

# FX (no key)
FX_URL = "https://open.er-api.com/v6/latest/USD"

# Gold (no key) - XAU price in USD per troy ounce
GOLD_URL = "https://api.gold-api.com/price/XAU"

OUT_PATH = "prices.json"
SCRIPT_VERSION = "v5-api-based-2025-12-30"

TROY_OUNCE_TO_GRAM = 31.1034768


def now_colombo_iso() -> str:
    dt = datetime.now(timezone(TZ_OFFSET))
    return dt.isoformat(timespec="seconds")


def http_get_json(url: str) -> dict:
    r = requests.get(url, timeout=30, headers={"User-Agent": "sl-market-friend-bot/1.0"})
    r.raise_for_status()
    return r.json()


def http_get_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "sl-market-friend-bot/1.0"})
    r.raise_for_status()
    return r.text


# -----------------------
# Fuel: Ceypetco parsing
# -----------------------
def parse_ceypetco_fuel(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)

    def find_price_effect(product_name: str):
        pattern = re.compile(
            rf"{re.escape(product_name)}.*?Rs\.\s*([\d,]+(?:\.\d+)?)\s*.*?Effect from:\s*([0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}})",
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
        out[key] = {"price_lkr_per_l": None, "effective_from": None}
        res = find_price_effect(name)
        if res:
            out[key]["price_lkr_per_l"], out[key]["effective_from"] = res
    return out


# -----------------------
# FX: ExchangeRate-API (no key)
# -----------------------
def fetch_fx_usd_gbp_eur_to_lkr() -> dict:
    data = http_get_json(FX_URL)

    if data.get("result") != "success":
        raise RuntimeError(f"FX API returned non-success: {data}")

    rates = data.get("rates") or {}

    usd_lkr = rates.get("LKR")
    usd_gbp = rates.get("GBP")
    usd_eur = rates.get("EUR")

    if not usd_lkr or not usd_gbp or not usd_eur:
        raise RuntimeError("Missing required FX rates: LKR/GBP/EUR not found in FX response")

    # Cross rates
    gbp_lkr = float(usd_lkr) / float(usd_gbp)
    eur_lkr = float(usd_lkr) / float(usd_eur)

    # We only have indicative/mid from this API; buy/sell are not provided.
    return {
        "usd_lkr_spot": {"indicative": round(float(usd_lkr), 4), "buy": None, "sell": None},
        "gbp_lkr": {"indicative": round(gbp_lkr, 4), "buy": None, "sell": None},
        "eur_lkr": {"indicative": round(eur_lkr, 4), "buy": None, "sell": None},
    }


# -----------------------
# Gold: Gold-API (no key) + convert to LKR/gram
# -----------------------
def fetch_gold_lkr_per_gram(usd_lkr: float) -> dict:
    data = http_get_json(GOLD_URL)

    # Try common fields defensively
    usd_per_oz = data.get("price") or data.get("value") or data.get("rate")
    if usd_per_oz is None:
        raise RuntimeError(f"Gold API response missing price field: {data}")

    usd_per_oz = float(usd_per_oz)
    lkr_per_oz = usd_per_oz * float(usd_lkr)

    lkr_per_gram_24k = lkr_per_oz / TROY_OUNCE_TO_GRAM
    lkr_per_gram_22k = lkr_per_gram_24k * (22.0 / 24.0)

    return {
        "lkr_per_gram_24k": round(lkr_per_gram_24k, 2),
        "lkr_per_gram_22k": round(lkr_per_gram_22k, 2),
    }


def main():
    last_updated = now_colombo_iso()

    payload = {
        "app": "SL Market Friend",
        "tz": "Asia/Colombo",
        "lastUpdated": last_updated,
        "sources": {"fuel": CEYPETCO_URL, "fx": FX_URL, "gold": GOLD_URL},
        "fuel": {
            "petrol_92": {"price_lkr_per_l": None, "effective_from": None},
            "petrol_95": {"price_lkr_per_l": None, "effective_from": None},
            "diesel_auto": {"price_lkr_per_l": None, "effective_from": None},
            "diesel_super": {"price_lkr_per_l": None, "effective_from": None},
            "kerosene": {"price_lkr_per_l": None, "effective_from": None},
        },
        "fx": {
            "usd_lkr_spot": {"indicative": None, "buy": None, "sell": None},
            "gbp_lkr": {"indicative": None, "buy": None, "sell": None},
            "eur_lkr": {"indicative": None, "buy": None, "sell": None},
        },
        "gold": {
            "lkr_per_gram_24k": None,
            "lkr_per_gram_22k": None,
            "notes": "Indicative rates; jewellery shop rates may vary.",
        },
        "debug": {"updatedBy": "github-actions", "runAt": last_updated, "scriptVersion": SCRIPT_VERSION},
    }

    # Fuel
    try:
        payload["fuel"] = parse_ceypetco_fuel(http_get_html(CEYPETCO_URL))
    except Exception as e:
        payload["debug"]["fuelError"] = str(e)

    # FX
    usd_lkr = None
    try:
        payload["fx"] = fetch_fx_usd_gbp_eur_to_lkr()
        usd_lkr = payload["fx"]["usd_lkr_spot"]["indicative"]
    except Exception as e:
        payload["debug"]["fxError"] = str(e)

    # Gold
    try:
        if usd_lkr is None:
            raise RuntimeError("USD->LKR missing; cannot compute gold.")
        g = fetch_gold_lkr_per_gram(float(usd_lkr))
        payload["gold"]["lkr_per_gram_24k"] = g["lkr_per_gram_24k"]
        payload["gold"]["lkr_per_gram_22k"] = g["lkr_per_gram_22k"]
    except Exception as e:
        payload["debug"]["goldError"] = str(e)

    # Write JSON to repo root
    out_dir = os.path.dirname(OUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Updated {OUT_PATH} at {payload['lastUpdated']}")


if __name__ == "__main__":
    main()
