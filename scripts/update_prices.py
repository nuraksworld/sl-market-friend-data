import json
from datetime import datetime, timezone, timedelta

OUT_PATH = "prices.json"
TZ_OFFSET = timedelta(hours=5, minutes=30)

def now_colombo_iso():
    return datetime.now(timezone(TZ_OFFSET)).isoformat(timespec="seconds")

payload = {
    "app": "SL Market Friend",
    "tz": "Asia/Colombo",
    "lastUpdated": now_colombo_iso(),
    "debug": {
        "updatedBy": "github-actions",
        "message": "TEST WRITE SUCCESS"
    }
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)

print("TEST prices.json written")
