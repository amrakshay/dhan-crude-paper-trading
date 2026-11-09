"""STEP 0d -- does Dhan's expired-options endpoint exist, and what does it hold?

THE ONLY MODULE in this experiment that names the endpoint below. It is a
read-only market-data endpoint (documented at
https://dhanhq.co/docs/v2/expired-options-data/, read 2026-10-03): rolling
minute bars for expired option contracts, addressed by strike relative to ATM.
It is NOT in the backend's allowlist and is not added there: that is the
operator's call. It is a POST like `/charts/intraday`, and it sends only the
contract description; nothing here can reach an order, funds or holdings path.

A PROBE, not a download: a handful of requests, printed and cached under
data/cache/probe/. Run from backend/ so the application's stored (encrypted)
token is overlaid exactly as the server sees it:

    cd backend && CONFIG_PATH=conf .venv/bin/python \
        ../experiments/bear-intraday/scripts/probe_expired_options.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
BACKEND = EXP.parent.parent / "backend"
CACHE = EXP / "data" / "cache" / "probe"
sys.path.insert(0, str(BACKEND))

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src import config_utils  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402

ROLLING_OPTION_URL = "https://api.dhan.co/v2/charts/rollingoption"
IST = timezone(timedelta(hours=5, minutes=30))
ALL_FIELDS = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]

# (label, securityId, expiryFlag, expiryCode, strike, side, from, to[exclusive])
PROBES = [
    ("NIFTY wk 2019-08", "13", "WEEK", 1, "ATM", "PUT", "2019-08-01", "2019-08-10"),
    ("NIFTY wk 2021-06", "13", "WEEK", 1, "ATM", "PUT", "2021-06-01", "2021-06-10"),
    ("NIFTY wk 2021-11", "13", "WEEK", 1, "ATM", "PUT", "2021-11-01", "2021-11-10"),
    ("NIFTY wk 2023-03", "13", "WEEK", 1, "ATM", "PUT", "2023-03-01", "2023-03-10"),
    ("NIFTY wk 2025-03", "13", "WEEK", 1, "ATM", "PUT", "2025-03-03", "2025-03-12"),
    ("NIFTY wk 2025-03 ATM-2", "13", "WEEK", 1, "ATM-2", "PUT", "2025-03-03", "2025-03-12"),
    ("NIFTY wk 2025-03 code2", "13", "WEEK", 2, "ATM", "PUT", "2025-03-03", "2025-03-12"),
    ("BANKNIFTY mo 2022-06", "25", "MONTH", 1, "ATM", "PUT", "2022-06-01", "2022-06-10"),
]


async def apply_stored_settings() -> None:
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()


def summarise(label: str, payload: dict) -> dict:
    side = (payload.get("data") or {}).get("pe") or (payload.get("data") or {}).get("ce") or {}
    ts = side.get("timestamp") or []
    out = {"label": label, "bars": len(ts)}
    if not ts:
        return out
    t = [datetime.fromtimestamp(x, IST) for x in ts]
    days = sorted({x.date() for x in t})
    out.update({
        "first": t[0].isoformat(), "last": t[-1].isoformat(), "days": len(days),
        "bars_first_day": sum(1 for x in t if x.date() == days[0]),
        "first_bar_time": min(x.time() for x in t).isoformat(),
        "last_bar_time": max(x.time() for x in t).isoformat(),
    })
    for f in ("strike", "spot", "close", "iv", "oi", "volume"):
        v = [x for x in (side.get(f) or []) if x is not None]
        out[f] = {"n": len(v), "min": min(v) if v else None, "max": max(v) if v else None,
                  "distinct": len(set(v)) if v else 0}
    return out


async def main() -> None:
    await apply_stored_settings()
    cid = config_utils.get_property_value("dhan.client_id", "") or ""
    tok = config_utils.get_property_value("dhan.access_token", "") or ""
    if not (cid and tok):
        print("No Dhan credentials configured; nothing probed.")
        return
    headers = {"access-token": tok, "client-id": cid,
               "Content-Type": "application/json", "Accept": "application/json"}
    CACHE.mkdir(parents=True, exist_ok=True)
    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for label, sid, flag, code, strike, side, frm, to in PROBES:
            body = {"exchangeSegment": "NSE_FNO", "interval": "5", "securityId": sid,
                    "instrument": "OPTIDX", "expiryFlag": flag, "expiryCode": code,
                    "strike": strike, "drvOptionType": side, "requiredData": ALL_FIELDS,
                    "fromDate": frm, "toDate": to}
            r = await client.post(ROLLING_OPTION_URL, json=body, headers=headers)
            row = {"label": label, "http": r.status_code}
            try:
                payload = r.json()
            except ValueError:
                payload = {"text": r.text[:300]}
            if r.status_code == 200:
                row.update(summarise(label, payload))
            else:
                row["error"] = json.dumps(payload)[:300]
            (CACHE / f"{label.replace(' ', '_')}.json").write_text(json.dumps(
                {"request": body, "status": r.status_code, "response": payload}))
            print(json.dumps(row, default=str))
            results.append(row)
            if r.status_code in (401, 403):
                # Every later request would fail the same way; stop rather than
                # send the rest with a token Dhan has just refused.
                print("Token refused; stopping. Save a fresh token on the Settings page.")
                break
            time.sleep(1.1)
    (EXP / "out" / "probe_expired_options.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
