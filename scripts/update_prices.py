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

# FX (no key): ExchangeRate-API Open Access (updates daily)
FX_URL = "https://open.er-api.com/v6/latest/USD"

# Gold (no key): Gold-API (XAU price in USD per oz)
GOLD_URL = "https://api.gold-api.com/price/XAU"

# Output JSON (for GitHub Pages repo: public/prices.json)
OUT_PATH = "public/prices.json"

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
# Fuel (CEYPETCO) parsing
# -----------------------
def parse_ceypetco_fuel(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)

    def find_price_effect(product_name: str):
        # Example pattern in page: "Rs. 294.00" and "Effect from: 31-10-2025"
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

    # keep schema stable if site changes
    if "kerosene" not in out:
        out["kerosene"] = {"price_lkr_per_l": None, "effective_from": None}

    return out


# -----------------------
# FX (Open ER API)
# -----------------------
def fetch_fx_usd_gbp_eur_to_lkr():
    """
    We fetch base USD rates (USD->LKR).
    For GBP->LKR and EUR->LKR we derive using cross rates:
      GBP->LKR = (USD->LKR) / (USD->GBP)
      EUR->LKR = (USD->LKR) / (USD->EUR)
    """
    data = http_get_json(FX_URL)

    if data.get("result") != "success":
        raise RuntimeError(f"FX API returned non-success: {data}")

    rates = data.get("rates") or {}
    usd_lkr = rates.get("LKR")
    usd_gbp = rates.get("GBP")
    usd_eur = rates.get("EUR")

    if not usd_lkr or not usd_gbp or not usd_eur:
        raise RuntimeError(f"Missing required FX rates in response: LKR/GBP/EUR not found")

    gbp_lkr = usd_lkr / usd_gbp
    eur_lkr = usd_lkr / usd_eur

    # We only have "mid/indicative" from this API; no buy/sell.
    return {
        "usd_lkr_spot": {"indicative": round(float(usd_lkr), 4), "buy": None, "sell": None},
        "gbp_lkr": {"indicative": round(float(gbp_lkr), 4), "buy": None, "sell": None},
        "eur_lkr": {"indicative": round(float(eur_lkr), 4), "buy": None, "sell": None},
    }


# -----------------------
# Gold (Gold-API) + FX conversion
# -----------------------
def fetch_gold_lkr_per_gram(fx_usd_lkr: float):
    """
    Gold-API returns XAU price in USD per oz.
    Convert to LKR/gram:
      LKR_per_oz = USD_per_oz * USD_to_LKR
      LKR_per_gram = LKR_per_oz / 31.1034768
    """
    data = http_get_json(GOLD_URL)

    # Common fields observed in such APIs: "price" or similar.
    # We'll be defensive:
    usd_per_oz = data.get("price") or data.get("value") or data.get("rate")
    if usd_per_oz is None:
        raise RuntimeError(f"Gold API response missing price fields: {data}")

    usd_per_oz = float(usd_per_oz)
    lkr_per_oz = usd_per_oz * float(fx_usd_lkr)
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
        "sources": {
            "fuel": CEYPETCO_URL,
            "fx": FX_URL,               # changed to stable API
            "gold": GOLD_URL,           # changed to stable API
        },
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
        "debug": {
            "updatedBy": "github-actions",
            "runAt": last_updated,
        },
    }

    # Fuel
    try:
        ce_html = http_get_html(CEYPETCO_URL)
        payload["fuel"] = parse_ceypetco_fuel(ce_html)
    except Exception as e:
        payload["debug"]["fuelError"] = str(e)

    # FX
    fx_usd_lkr = None
    try:
        fx_obj = fetch_fx_usd_gbp_eur_to_lkr()
        payload["fx"] = fx_obj
        fx_usd_lkr = fx_obj["usd_lkr_spot"]["indicative"]
    except Exception as e:
        payload["debug"]["fxError"] = str(e)

    # Gold (needs USD->LKR)
    try:
        if fx_usd_lkr is None:
            raise RuntimeError("FX USD->LKR missing, cannot compute LKR gold price.")
        gold_obj = fetch_gold_lkr_per_gram(fx_usd_lkr=float(fx_usd_lkr))
        payload["gold"]["lkr_per_gram_24k"] = gold_obj["lkr_per_gram_24k"]
        payload["gold"]["lkr_per_gram_22k"] = gold_obj["lkr_per_gram_22k"]
    except Exception as e:
        payload["debug"]["goldError"] = str(e)

    # Write output
    out_dir = os.path.dirname(OUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Updated {OUT_PATH} at {payload['lastUpdated']}")


if __name__ == "__main__":
    main()
