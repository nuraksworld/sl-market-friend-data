import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# ----------------------------
# Config
# ----------------------------
TZ_OFFSET = timedelta(hours=5, minutes=30)  # Asia/Colombo fixed offset

CEYPETCO_URL = "https://ceypetco.gov.lk/marketing-sales/"
CBSL_URL = "https://www.cbsl.gov.lk/en/rates-and-indicators/exchange-rates"

# Gold (disabled by default to avoid 403 + key exposure)
ENABLE_GOLD = False
GOLDPRICEZ_URL = "https://goldpricez.com/api/rates"
GOLDPRICEZ_KEY = os.getenv("GOLDPRICEZ_KEY", "").strip()

OUT_PATH = "prices.json"
SCRIPT_VERSION = "v4-2025-12-29"


def now_colombo_iso() -> str:
    dt = datetime.now(timezone(TZ_OFFSET))
    return dt.isoformat(timespec="seconds")


def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "sl-market-friend-bot/1.0"})
    r.raise_for_status()
    return r.text


# ----------------------------
# Fuel: Ceypetco page parsing (your current method works)
# ----------------------------
def parse_ceypetco_fuel(html: str):
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


# ----------------------------
# FX: CBSL parsing (table-based, more robust than regex)
# ----------------------------
def parse_cbsl_fx(html: str):
    soup = BeautifulSoup(html, "html.parser")

    def to_float(s: str):
        if s is None:
            return None
        s = s.strip().replace(",", "")
        return float(s) if re.match(r"^\d+(\.\d+)?$", s) else None

    default = {"indicative": None, "buy": None, "sell": None}

    # Find a table that likely contains currency rows
    tables = soup.find_all("table")
    target = None

    # Prefer tables containing USD and BUY/SELL-like words
    for t in tables:
        txt = t.get_text(" ", strip=True).upper()
        if "USD" in txt and ("BUY" in txt or "SELL" in txt or "INDICATIVE" in txt or "MIDDLE" in txt):
            target = t
            break

    # Fallback: any table containing USD
    if not target:
        for t in tables:
            if "USD" in t.get_text(" ", strip=True).upper():
                target = t
                break

    if not target:
        return {"usd_lkr_spot": default, "gbp_lkr": default, "eur_lkr": default}

    data_map = {}

    for r in target.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in r.find_all(["th", "td"])]
        if not cells:
            continue

        row_upper = " ".join(cells).upper()

        code = None
        for c in ("USD", "GBP", "EUR"):
            if re.search(rf"\b{c}\b", row_upper):
                code = c
                break
        if not code:
            continue

        # Extract numeric values from the row
        nums = []
        for v in cells:
            n = to_float(v)
            if n is not None:
                nums.append(n)

        # Heuristic mapping:
        # If the table has 3+ numbers, we map first 3 into indicative/buy/sell.
        indicative = nums[0] if len(nums) >= 1 else None
        buy = nums[1] if len(nums) >= 2 else None
        sell = nums[2] if len(nums) >= 3 else None

        data_map[code] = {"indicative": indicative, "buy": buy, "sell": sell}

    def get(code: str):
        return data_map.get(code, default)

    return {
        "usd_lkr_spot": get("USD"),
        "gbp_lkr": get("GBP"),
        "eur_lkr": get("EUR"),
    }


# ----------------------------
# Gold: disabled by default; safe error handling
# ----------------------------
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
    last_updated = now_colombo_iso()

    payload = {
        "app": "SL Market Friend",
        "tz": "Asia/Colombo",
        "lastUpdated": last_updated,
        "sources": {"fuel": CEYPETCO_URL, "fx": CBSL_URL, "gold": "https://goldpricez.com/about/api"},
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
            "scriptVersion": SCRIPT_VERSION,
        },
    }

    # Fuel
    try:
        ce_html = fetch_html(CEYPETCO_URL)
        payload["fuel"] = parse_ceypetco_fuel(ce_html)
    except Exception as e:
        payload["debug"]["fuelError"] = str(e)

    # FX
    try:
        cbsl_html = fetch_html(CBSL_URL)
        payload["fx"] = parse_cbsl_fx(cbsl_html)

        if payload["fx"]["usd_lkr_spot"]["indicative"] is None:
            payload["debug"]["fxHint"] = "CBSL table found, but USD values not extracted. Layout may have changed."
    except Exception as e:
        payload["debug"]["fxError"] = str(e)

    # Gold (disabled by default to avoid 403 + key exposure)
    if ENABLE_GOLD:
        try:
            gold = fetch_gold_lkr_per_gram()
            payload["gold"]["lkr_per_gram_24k"] = gold["lkr_per_gram_24k"]
            payload["gold"]["lkr_per_gram_22k"] = gold["lkr_per_gram_22k"]
        except Exception as e:
            # DO NOT leak URL (which contains the key). Store short message only.
            payload["debug"]["goldError"] = str(e).split(" for url:")[0]
    else:
        payload["debug"]["goldSkipped"] = True

    # Safe directory creation (root OUT_PATH => no folder)
    out_dir = os.path.dirname(OUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Updated {OUT_PATH} at {payload['lastUpdated']}")


if __name__ == "__main__":
    main()
