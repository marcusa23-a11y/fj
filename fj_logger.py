#!/usr/bin/env python3
"""
fj_logger.py — FinancialJuice RSS capture, single-shot.

The FJ RSS feed holds only the 100 most recent headlines. This runs once,
appends anything new to a daily CSV, and exits. Run it on a schedule and
nothing rolls off the window unseen.

Written for GitHub Actions but runs anywhere. Stdlib only, no installs.

Layout produced:
    daily/YYYY-MM-DD.csv   one file per ET calendar day
    recent_48h.csv         rolling last 48 hours
    index.csv              catalogue of every daily file

Files are split on EASTERN date, not UTC. A 20:37 ET headline belongs to that
evening's session, not to the next UTC day.

Columns:
    guid            FJ article id (sequential; also the archive URL key)
    published_et    Publication time from the feed, converted to US/Eastern
    published_utc   Same instant in UTC
    captured_utc    When this script first saw the item
    delay_seconds   captured_utc - published_utc
    title           Headline text
    link            Permanent article URL
"""

import csv
import glob
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    EASTERN = timezone(timedelta(hours=-5))

FEED_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"

# FJ returns HTTP 403 to a default Python user-agent. A browser UA is required.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Cache-Control": "no-cache",
}

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY_DIR = os.path.join(HERE, "daily")
INDEX_PATH = os.path.join(HERE, "index.csv")
RECENT_PATH = os.path.join(HERE, "recent_48h.csv")
STATE_PATH = os.path.join(HERE, ".fj_state")

FIELDS = ["guid", "published_et", "published_utc", "captured_utc",
          "delay_seconds", "title", "link"]

# The RSS window spans at most ~48h. Reading 5 days of history to build the
# dedupe set is a large margin and stays fast as the archive grows.
LOOKBACK_DAYS = 5

RSS_DATE_FORMATS = ["%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"]


def parse_rss_date(raw):
    """RFC-822 date string -> aware UTC datetime, or None."""
    raw = (raw or "").strip()
    for fmt in RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def fetch_feed(timeout=30, attempts=3):
    """Fetch the feed, retrying transient failures."""
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(FEED_URL, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last = exc
            if n < attempts - 1:
                import time
                time.sleep(3 * (n + 1))
    raise last


def parse_items(xml_text):
    """Feed XML -> list of dicts, newest first."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  ! feed did not parse as XML: {exc}")
        return out

    for item in root.iter("item"):
        def text(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        guid = text("guid")
        if not guid:
            continue

        title = re.sub(r"^FinancialJuice:\s*", "", text("title"))
        out.append({
            "guid": guid,
            "title": " ".join(title.split()),
            "link": text("link"),
            "published": parse_rss_date(text("pubDate")),
        })
    return out


def daily_path(et_dt):
    return os.path.join(DAILY_DIR, f"{et_dt:%Y-%m-%d}.csv")


def read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception as exc:
        print(f"  ! could not read {os.path.basename(path)}: {exc}")
        return []


def load_recent_guids():
    """GUIDs already stored, plus the highest id seen, from recent daily files."""
    seen, highest = set(), None
    today = datetime.now(EASTERN).date()
    for back in range(LOOKBACK_DAYS):
        day = today - timedelta(days=back)
        for row in read_csv(os.path.join(DAILY_DIR, f"{day:%Y-%m-%d}.csv")):
            g = (row.get("guid") or "").strip()
            if not g:
                continue
            seen.add(g)
            if g.isdigit():
                n = int(g)
                highest = n if highest is None else max(highest, n)
    return seen, highest


def read_state():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            v = fh.read().strip()
        return int(v) if v.isdigit() else None
    except Exception:
        return None


def write_state(highest):
    if highest is None:
        return
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        fh.write(str(highest))


def append_rows(rows_by_day):
    """Append new rows to their daily files, keeping each file time-sorted."""
    for day_path, rows in rows_by_day.items():
        existing = read_csv(day_path)
        combined = existing + rows
        combined.sort(key=lambda r: (r.get("published_utc") or "", r.get("guid") or ""))
        os.makedirs(os.path.dirname(day_path), exist_ok=True)
        with open(day_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(combined)


def rebuild_recent():
    """Rolling 48-hour file, assembled from the last few daily files."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    rows = []
    today = datetime.now(EASTERN).date()
    for back in range(4):
        day = today - timedelta(days=back)
        for row in read_csv(os.path.join(DAILY_DIR, f"{day:%Y-%m-%d}.csv")):
            stamp = row.get("published_utc") or ""
            try:
                dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if dt >= cutoff:
                rows.append(row)
    rows.sort(key=lambda r: r.get("published_utc") or "")
    with open(RECENT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def rebuild_index():
    """Catalogue every daily file so an agent can reach them from one URL."""
    entries = []
    for path in sorted(glob.glob(os.path.join(DAILY_DIR, "*.csv"))):
        rows = read_csv(path)
        if not rows:
            continue
        entries.append({
            "date_et": os.path.basename(path).replace(".csv", ""),
            "file": f"daily/{os.path.basename(path)}",
            "headlines": len(rows),
            "first_et": rows[0].get("published_et", ""),
            "last_et": rows[-1].get("published_et", ""),
        })
    entries.sort(key=lambda e: e["date_et"], reverse=True)
    with open(INDEX_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date_et", "file", "headlines",
                                           "first_et", "last_et"])
        w.writeheader()
        w.writerows(entries)
    return entries


def main():
    os.makedirs(DAILY_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)

    print(f"FinancialJuice RSS capture — {now:%Y-%m-%d %H:%M:%S} UTC")

    try:
        items = parse_items(fetch_feed())
    except Exception as exc:
        print(f"  FETCH FAILED: {str(exc)[:120]}")
        print("  Nothing written. The next scheduled run will catch up "
              "(the feed holds 100 items).")
        # Exit 0 on purpose. A blip should not show as a failed run, or real
        # failures stop standing out.
        return 0

    if not items:
        print("  Feed returned no items. Nothing written.")
        return 0

    seen, highest_from_files = load_recent_guids()
    highest = read_state()
    if highest_from_files is not None:
        highest = max(highest, highest_from_files) if highest else highest_from_files

    new = [i for i in items if i["guid"] not in seen]

    # Gap check: if every item in the window is new and the lowest id sits well
    # above the last one on file, headlines were published while nothing ran.
    gap = None
    if new and highest and len(new) == len(items):
        ids = [int(i["guid"]) for i in items if i["guid"].isdigit()]
        if ids and min(ids) > highest + 1:
            gap = (highest + 1, min(ids) - 1)

    rows_by_day, delays = {}, []
    for item in reversed(new):  # oldest first
        pub = item["published"]
        if pub is None:
            continue
        pub_et = pub.astimezone(EASTERN)
        delay = round((now - pub).total_seconds(), 1)
        delays.append(delay)

        rows_by_day.setdefault(daily_path(pub_et), []).append({
            "guid": item["guid"],
            "published_et": pub_et.strftime("%Y-%m-%d %H:%M:%S"),
            "published_utc": pub.strftime("%Y-%m-%d %H:%M:%S"),
            "captured_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
            "delay_seconds": delay,
            "title": item["title"],
            "link": item["link"],
        })

        if item["guid"].isdigit():
            n = int(item["guid"])
            highest = n if highest is None else max(highest, n)

    if rows_by_day:
        append_rows(rows_by_day)
    recent_n = rebuild_recent()
    index = rebuild_index()
    write_state(highest)

    total = sum(e["headlines"] for e in index)
    added = sum(len(v) for v in rows_by_day.values())

    print("=" * 60)
    print("RUN SUMMARY")
    print("=" * 60)
    print(f"  In feed window     : {len(items)}")
    print(f"  New this run       : {added}")
    print(f"  Days on file       : {len(index)}")
    print(f"  Total headlines    : {total}")
    print(f"  Rolling 48h file   : {recent_n}")
    if delays:
        o = sorted(delays)
        cold = not seen  # nothing on file before this run
        print(f"  Delivery lag       : median {o[len(o)//2]:.0f}s | "
              f"min {o[0]:.0f}s | max {o[-1]:.0f}s")
        if cold:
            print("     (cold start — this is the existing backlog, not "
                  "delivery lag. Ignore until the next run.)")
    if gap:
        print(f"  GAP DETECTED       : ids {gap[0]}-{gap[1]} "
              f"({gap[1]-gap[0]+1} ids) never captured")
        print("     Recoverable as headlines via archive walk; times are lost.")
    else:
        print("  Gaps               : none")
    if added:
        for path in sorted(rows_by_day):
            print(f"    wrote {len(rows_by_day[path]):>3} -> daily/{os.path.basename(path)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
