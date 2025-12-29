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

OUT_PATH = "prices.json"


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


def parse_cbsl_fx(html: str):
    """
    Robust FX parser:
    - CBSL page structure changes frequently; regex on plain text is fragile.
    - We scan tables, find rows containing USD/GBP/EUR, and extract numeric columns.
    """
    soup = BeautifulSoup(html, "html.parser")

    def to_float(s):
        if s is None:
            return None
        s = s.strip().replace(",", "")
        return float(s) if re.match(r"^\d+(\.\d+)?$", s) else None

    # Pick a table that contains USD (prefer tables that include BUY/SELL words)
    tables = soup.find_all("table")
    target = None
    for t in tables:
        txt = t.get_text(" ", strip=True).upper()
        if "USD" in txt and ("BUY" in txt or "SELL" in txt or "INDICATIVE" in txt):
            target = t
            break
    if not target:
        for t in tables:
            txt = t.get_text(" ", strip=True).upper()
            if "USD" in txt:
                target = t
                break

    default = {"indicative": None, "buy": None, "sell": None}
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

        # Collect numeric values in row (in order)
        nums = []
        for v in cells:
            n = to_float(v)
            if n is not None:
                nums.append(n)

        # Heuristic: take first 3 numbers as indicative/buy/sell if available
        indicative = nums[0] if len(nums) >= 1 else None
        buy = nums[1] if len(nums) >= 2 else None
        sell = nums[2] if len(nums) >= 3 else None

        data_map[code] = {"indicative": indicative, "buy": buy, "sell": sell}

    def get(code):
        return data_map.get(code, {"indicative": None, "buy": None, "sell": None})

    return {
        "usd_lkr_spot": get("USD"),
        "gbp_lkr": get("GBP"),
        "eur_lkr": get("EUR"),
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
        "debug": {"updatedBy": "github-actions", "runAt": last_updated},
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
    except Exception as e:
        payload["debug"]["fxError"] = str(e)

    # Gold (optional)
    try:
        gold = fetch_gold_lkr_per_gram()
        payload["gold"]["lkr_per_gram_24k"] = gold["lkr_per_gram_24k"]
        payload["gold"]["lkr_per_gram_22k"] = gold["lkr_per_gram_22k"]
    except Exception as e:
        payload["debug"]["goldError"] = str(e)

    # Safe directory creation even when OUT_PATH has no folder
    out_dir = os.path.dirname(OUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Updated {OUT_PATH} at {payload['lastUpdated']}")


if __name__ == "__main__":
    main()
