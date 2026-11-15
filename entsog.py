"""ENTSOG Transparency Platform access, shared by the app and the daily fetch script.

No Streamlit imports here, so GitHub Actions can run it without Streamlit.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

URL = "https://transparency.entsog.eu/api/v1/operationalData.json"
CHUNK_DAYS = 180  # one request covers at most this many days

# (group, operatorKey, pointKey, direction, label)
SERIES = [
    ("INC", "LV-TSO-0001", "UGS-00029", "entry", "Inčukalns withdrawal"),
    ("GIPL", "LT-TSO-0001", "ITP-00556", "entry", "Santaka / GIPL (PL→LT)"),
    ("KLP", "LT-TSO-0001", "LNG-00030", "entry", "Klaipėda LNG"),
    ("FI_LNG", "FI-TSO-0003", "LNG-00072", "entry", "Inkoo LNG"),
    ("FI_LNG", "FI-TSO-0003", "LNG-00011", "entry", "Hamina LNG"),
]


def fetch_start(today):
    """First day stored: 1 January two years back covers the 24-month window."""
    return date(today.year - 2, 1, 1)


def series_keys():
    """Unique (operator, point, direction) in SERIES order."""
    return list(dict.fromkeys((op, pt, d) for _, op, pt, d, _ in SERIES))


def _request(params):
    """GET with retries. Raises RuntimeError if all attempts fail."""
    err = "unknown error"
    for attempt in range(4):
        try:
            r = requests.get(URL, params=params, timeout=120)
            if r.status_code == 200:
                return r.json().get("operationalData", [])
            err = f"HTTP {r.status_code}"
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break  # retrying will not help
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(err)


def chunks(start, end, days=CHUNK_DAYS):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def fetch_series(operator, point, direction, start_str, end_str):
    """Daily Physical Flow (kWh/d) for one operator/point/direction.

    Returns {"days": {day: kWh}, "dupes": n}. Raises if any chunk fails, so
    partial results are never used.
    """
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    def one(rng):
        a, b = rng
        return _request({
            "indicator": "Physical Flow",
            "periodType": "day",
            "from": a.isoformat(),
            "to": b.isoformat(),
            "pointKey": point,
            "directionKey": direction,
            "limit": -1,
        })

    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(one, list(chunks(start, end))))

    days, dupes = {}, 0
    for recs in parts:
        for x in recs:
            if (x.get("operatorKey") != operator
                    or x.get("pointKey") != point
                    or x.get("directionKey") != direction):
                continue
            day = str(x.get("periodFrom", ""))[:10]
            try:
                value = float(x.get("value") or 0)
            except (TypeError, ValueError):
                value = 0.0
            if day in days:
                dupes += 1
            days[day] = value
    return {"days": days, "dupes": dupes}
