#!/usr/bin/env python3
"""
Matkahuolto daily tracker → Telegram alert
=========================================

• Pulls every consignment whose latest event is within the last LOOKBACK_DAYS
  (default 4 calendar days).  Works with *any* of Matkahuolto’s JSON layouts:
    1. top-level list  [ {eventId…}, … ]
    2. {"consignments":[…]} or {"MHTrackingResults":[…]}
    3. {"MH67…":{"events":[…]}, "MH30…":{…}}  (dict-of-dicts)
    4. flat list of single events  [{eventCode…},{eventCode…}]  ← NEW
• Keeps a tiny SQLite cache so each consignment is tracked forever.
• Sends ONE Telegram message per run:
     ✅  All the packages are on their way as normal.
     ⚠️  List every shipment whose status hasn’t changed for
         STALE_BUSINESS_DAYS Finnish business days (default 2)
         and whose last event code is **not** in {55, 56, 57, 60}.
"""

import os, sys, sqlite3, logging
from datetime import datetime, timedelta, timezone

import requests
from workalendar.europe import Finland
from telegram import Bot

# ────────────────────────── dotenv ─────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

# ────────────────────────── config ────────────────────────────────────
MH_USER   = os.getenv("MH_USER")
MH_PASS   = os.getenv("MH_PASS")
TG_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TG_CHAT   = os.getenv("TELEGRAM_CHAT_ID")

LOOKBACK  = int(os.getenv("LOOKBACK_DAYS", "4"))
STALE_D   = int(os.getenv("STALE_BUSINESS_DAYS", "2"))
REQ_TIMEOUT = int(os.getenv("MH_TIMEOUT", "90"))
ENDPOINT  = os.getenv("MH_ENDPOINT",
           "https://extservices.matkahuolto.fi/mpaketti/public/tracking")
DB_PATH   = os.getenv("DB_PATH", "mh_cache.sqlite")

FINAL_OK_CODES = {"55", "56", "57", "60"}

if not all((MH_USER, MH_PASS, TG_TOKEN, TG_CHAT)):
    sys.exit("Missing required environment variables – see docstring.")

# ────────────────────────── helpers ───────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")
bot = Bot(TG_TOKEN)
cal = Finland()

def business_days_between(a: datetime, b: datetime) -> int:
    """Finnish business days strictly between a and b (inclusive on start)."""
    delta = cal.get_working_days_delta(a.date(), b.date())
    days  = delta if isinstance(delta, int) else delta.days
    return max(days - 1, 0) 

def fetch_window(days_back: int):
    to_dt   = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days_back)
    params  = {"from": from_dt.isoformat(timespec="seconds"),
               "to"  :  to_dt.isoformat(timespec="seconds")}

    for attempt in (1, 2):               # one retry
        try:
            r = requests.get(
                ENDPOINT, params=params,
                auth=(MH_USER, MH_PASS),
                headers={"Accept": "application/json"},
                timeout=REQ_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ReadTimeout:
            if attempt == 1:
                logging.warning("Read-timeout after %ss → retry once …", REQ_TIMEOUT)
            else:
                raise

def extract_consignments(blob):
    """
    Normalise Matkahuolto's 4 possible response shapes into a list of
    consignment-like dicts with an 'events':[ … ] key.
    """
    # 1. blob already a list of consignment dicts?
    if isinstance(blob, list) and blob and isinstance(blob[0], dict) and "events" in blob[0]:
        return blob

    # 2. {"consignments":[…]} or {"MHTrackingResults":[…]}
    if isinstance(blob, dict):
        for key in ("consignments", "MHTrackingResults"):
            if key in blob and isinstance(blob[key], list):
                return blob[key]

        # 3. dict-of-dicts  {"MH67…": {events:[…]}, …}
        if all(isinstance(v, dict) for v in blob.values()):
            return list(blob.values())

        # 4. flat list of **events** inside some wrapper → collapse below
        for v in blob.values():
            if isinstance(v, list) and v and "eventCode" in v[0]:
                return collapse_events_to_consignments(v)

    # 5. top-level flat list of **events**
    if isinstance(blob, list) and blob and "eventCode" in blob[0]:
        return collapse_events_to_consignments(blob)

    return []

def collapse_events_to_consignments(events):
    """
    Convert a list of standalone event dicts into
    [{'ShipmentNumber':'MH…', 'events':[ latest_event_dict ]}, …].
    Only the *latest* event per shipment is kept.
    """
    latest = {}
    for e in events:
        cid = (e.get("shipmentNumber") or e.get("ShipmentNumber") or
               e.get("parcelNumber")   or e.get("ParcelNumber"))
        if not cid or "eventTime" not in e:
            continue
        if (cid not in latest or e["eventTime"] > latest[cid]["eventTime"]):
            latest[cid] = e
    return [{"ShipmentNumber": k, "events": [v]} for k, v in latest.items()]

def latest_event(consignment):
    """Return (timestamp, code) of the consignment's latest event."""
    evts = consignment.get("events") or consignment.get("MHTrackingEvents") or []
    if not evts:
        return None, None
    last = max(evts, key=lambda e: e["eventTime"])
    return last["eventTime"], (last.get("eventCode") or last.get("event_code"))

# ────────────────────────── SQLite cache ───────────────────────────────
def ensure_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS shipments(
                          id TEXT PRIMARY KEY,
                          last_time TEXT,
                          last_status TEXT)""")
        con.commit()

def read_cache():
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute("SELECT id, last_time, last_status FROM shipments")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

def upsert(cid, tstamp, status):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""INSERT OR REPLACE INTO shipments(id, last_time, last_status)
                       VALUES (?,?,?)""", (cid, tstamp, status))
        con.commit()

def alert(text: str):
    bot.send_message(chat_id=TG_CHAT, text=text)

# ────────────────────────── main ───────────────────────────────────────
def main():
    ensure_db()
    cache = read_cache()
    stuck = []

    data = fetch_window(LOOKBACK)
    consignments = extract_consignments(data)

    for c in consignments:
        cid = (c.get("id") or c.get("ShipmentNumber") or c.get("ShipmentId") or
               c.get("shipmentNumber") or c.get("ParcelNumber") or c.get("parcelNumber"))
        if not cid:
            continue

        last_time_str, last_code = latest_event(c)
        if not last_time_str:
            continue

        last_time = datetime.fromisoformat(last_time_str)
        cached_time_str, _ = cache.get(cid, (None, None))
        if cached_time_str != last_time_str:
            upsert(cid, last_time_str, last_code)

        age_bdays = business_days_between(last_time,
                                          datetime.now(last_time.tzinfo))

        if age_bdays >= STALE_D and (last_code not in FINAL_OK_CODES):
            stuck.append(f"• {cid}: {age_bdays} business days unchanged "
                         f"(last {last_code} @ {last_time_str})")

    moving_ids = [cid for cid, (_, code) in cache.items()
                  if code not in FINAL_OK_CODES]
    moving_total = len(moving_ids)
    stuck_ids    = [line.split()[1].rstrip(':') for line in stuck]  # extract IDs
    stuck_total  = len(stuck_ids)

    header = f"{moving_total} package{'s' if moving_total!=1 else ''} currently in transit 📦"

    if stuck_total == 0:
        alert(f"{header}\n✅ All those packages are on their way as normal.")
        logging.info("Sent green summary (%d moving, none stuck)", moving_total)
    else:
        alert(f"{header}\n⚠️ {stuck_total} package(s) may be delayed:\n"
              + "\n".join(f"• {cid}" for cid in stuck_ids))
        logging.info("Sent alert for %d stuck shipment(s)", stuck_total)

# ────────────────────────── entry ───────────────────────────────────────
if __name__ == "__main__":
    main()
