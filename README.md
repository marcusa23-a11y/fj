# fj — FinancialJuice headline archive

The FinancialJuice RSS feed holds only the **100 most recent headlines**. Anything
older rolls off and is unreachable. This repo polls the feed every 5 minutes and
appends new headlines to a permanent CSV archive, so the window stops mattering.

Runs on GitHub Actions. Nothing needs to be on locally.

## Layout

```
index.csv              catalogue of every daily file — start here
recent_48h.csv         rolling last 48 hours
daily/YYYY-MM-DD.csv   one file per Eastern calendar day
fj_logger.py           the poller
```

## Reading it

Replace `USER` with the GitHub account name.

| Want | URL |
|---|---|
| What files exist | `https://raw.githubusercontent.com/USER/fj/main/index.csv` |
| Last 48 hours | `https://raw.githubusercontent.com/USER/fj/main/recent_48h.csv` |
| A specific day | `https://raw.githubusercontent.com/USER/fj/main/daily/2026-07-19.csv` |

Give an AI agent the `index.csv` URL and it can reach every daily file from there.

## Columns

| Column | Meaning |
|---|---|
| `guid` | FJ article id. Sequential, and the key to the archive URL `/News/{guid}/x.aspx` |
| `published_et` | Publication time from the feed, US/Eastern, to the second |
| `published_utc` | Same instant in UTC |
| `captured_utc` | When the poller first saw the item |
| `delay_seconds` | `captured_utc` − `published_utc`. Feed delivery lag |
| `title` | Headline text |
| `link` | Permanent article URL |

Files split on **Eastern** date, not UTC — a 20:37 ET headline belongs to that
evening's session, not to the next UTC day.

## What this does not capture

The RSS feed carries no tags. There is no **Market Moving** flag, no red
breaking flag, and no source attribution. This archive answers *what was
published and exactly when*. It does not answer *what FinancialJuice marked as
important*. For that, copy-paste the window from the site.

## Gaps

If the workflow stops for a while, the next run reports the exact range of
missing article ids. Those headlines are recoverable one at a time from
`https://www.financialjuice.com/News/{id}/x.aspx` — any slug works, and a
browser user-agent header is required or the site returns 403. Those archive
pages carry the headline text but **no timestamp**.

## Running it locally

```
python fj_logger.py
```

Standard library only. No installs.
