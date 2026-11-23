"""Data: stock universes from NSE, bars from Yahoo (or imported from Kite), NSE surveillance and calendar.

Everything is cached under the workspace (default ~/marketfeed-screener):

    cache/universes/<name>.json  a stock list with sector (NSE's "Industry"), cap bucket, series, band
    cache/prices/<SYM>.json      daily bars {t, o, h, l, c, v}, dates as YYYY-MM-DD (IST trading day)
    cache/intraday/<tf>/<SYM>.json  5m / 15m / 1h bars, t as "YYYY-MM-DD HH:MM" (bar START, IST).
                                 Yahoo keeps only ~60 days of 5m/15m and ~2-3 years of 1h, so the cache
                                 grows every time data runs; Kite history can be imported for more.
    cache/nse/asm.json, gsm.json, events.json   NSE surveillance lists and board meetings ahead
    cache/nse/eod/<YYYYMMDD>.json   NSE bhavcopy + index closes, used where Yahoo has no close

Free sources only. Yahoo is unofficial and can lag; NSE blocks some networks at times.
"""

import concurrent.futures
import csv
import datetime as dt
import http.cookiejar
import io
import json
import os
import time
import urllib.error
import urllib.request

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
YAHOO_UA = "Mozilla/5.0 (compatible; marketfeed-screener/1.0)"   # a fake Chrome UA gets 429 from Yahoo
NSE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0 Safari/537.36")
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NSE_INDEX_CSV = "https://nsearchives.nseindia.com/content/indices/%s"
NSE_SEC_LIST = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"
NSE_COOKIE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"

# name -> (NSE file, label). Every list is a public NSE index constituents CSV.
UNIVERSES = {
    "nifty50": ("ind_nifty50list.csv", "Nifty 50"),
    "niftynext50": ("ind_niftynext50list.csv", "Nifty Next 50"),
    "nifty100": ("ind_nifty100list.csv", "Nifty 100"),
    "nifty200": ("ind_nifty200list.csv", "Nifty 200"),
    "nifty500": ("ind_nifty500list.csv", "Nifty 500"),
    "midcap100": ("ind_niftymidcap100list.csv", "Nifty Midcap 100"),
    "midcap150": ("ind_niftymidcap150list.csv", "Nifty Midcap 150"),
    "smallcap100": ("ind_niftysmallcap100list.csv", "Nifty Smallcap 100"),
    "smallcap250": ("ind_niftysmallcap250list.csv", "Nifty Smallcap 250"),
    "largemidcap250": ("ind_niftylargemidcap250list.csv", "Nifty LargeMidcap 250"),
    "midsmallcap400": ("ind_niftymidsmallcap400list.csv", "Nifty MidSmallcap 400"),
    "totalmarket": ("ind_niftytotalmarket_list.csv", "Nifty Total Market (750, incl. micro caps)"),
    "bank": ("ind_niftybanklist.csv", "Nifty Bank"),
    "financials": ("ind_niftyfinancelist.csv", "Nifty Financial Services"),
    "psubank": ("ind_niftypsubanklist.csv", "Nifty PSU Bank"),
    "it": ("ind_niftyitlist.csv", "Nifty IT"),
    "pharma": ("ind_niftypharmalist.csv", "Nifty Pharma"),
    "healthcare": ("ind_niftyhealthcarelist.csv", "Nifty Healthcare"),
    "auto": ("ind_niftyautolist.csv", "Nifty Auto"),
    "fmcg": ("ind_niftyfmcglist.csv", "Nifty FMCG"),
    "metal": ("ind_niftymetallist.csv", "Nifty Metal"),
    "realty": ("ind_niftyrealtylist.csv", "Nifty Realty"),
    "energy": ("ind_niftyenergylist.csv", "Nifty Energy"),
    "oilgas": ("ind_niftyoilgaslist.csv", "Nifty Oil & Gas"),
    "consumerdurables": ("ind_niftyconsumerdurableslist.csv", "Nifty Consumer Durables"),
    "media": ("ind_niftymedialist.csv", "Nifty Media"),
    "defence": ("ind_niftyindiadefence_list.csv", "Nifty India Defence"),
    "cpse": ("ind_niftycpselist.csv", "Nifty CPSE"),
    "fno": (None, "F&O stocks (NSE derivatives list)"),
    "custom": (None, "Your own list of symbols"),
}
UNIVERSE_ALIASES = {"niftytotalmarket": "totalmarket", "nifty150": "midcap150", "midcap": "midcap150",
                    "smallcap": "smallcap250", "nifty_bank": "bank", "banknifty": "bank", "niftyit": "it",
                    "fo": "fno", "f&o": "fno", "fnostocks": "fno"}
NSE_FO_LOTS = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
CAP_LISTS = {"large": "ind_nifty100list.csv", "mid": "ind_niftymidcap150list.csv",
             "small": "ind_niftysmallcap250list.csv"}

# Benchmarks and the market-regime inputs
INDEX_TICKERS = {
    "NIFTY500": "^CRSLDX",
    "NIFTY50": "^NSEI",
    "SENSEX": "^BSESN",
    "INDIAVIX": "^INDIAVIX",
}
# NSE sector indices on Yahoo (for the sector table; the ranking itself is built bottom-up)
SECTOR_INDEX_TICKERS = {
    "Nifty Bank": "^NSEBANK", "Nifty Fin Services": "NIFTY_FIN_SERVICE.NS", "Nifty IT": "^CNXIT",
    "Nifty Auto": "^CNXAUTO", "Nifty Pharma": "^CNXPHARMA", "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL", "Nifty Energy": "^CNXENERGY", "Nifty Realty": "^CNXREALTY",
    "Nifty PSU Bank": "^CNXPSUBANK", "Nifty Infra": "^CNXINFRA", "Nifty Consumption": "^CNXCONSUM",
    "Nifty PSE": "^CNXPSE", "Nifty MNC": "^CNXMNC",
}


class DataError(Exception):
    pass


# ---------------------------------------------------------------- paths

def paths(home):
    p = {
        "home": home,
        "cache": os.path.join(home, "cache"),
        "prices": os.path.join(home, "cache", "prices"),
        "intraday": os.path.join(home, "cache", "intraday"),
        "universes": os.path.join(home, "cache", "universes"),
        "nse": os.path.join(home, "cache", "nse"),
        "strategies": os.path.join(home, "strategies"),
        "positions": os.path.join(home, "positions.json"),
        "runs": os.path.join(home, "runs"),
        "backtests": os.path.join(home, "backtests"),
        "forward": os.path.join(home, "forward"),
    }
    for k in ("cache", "prices", "intraday", "universes", "nse", "strategies", "runs", "backtests", "forward"):
        os.makedirs(p[k], exist_ok=True)
    return p


def now_ist():
    return dt.datetime.now(IST)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------- HTTP

def http_get(url, ua, timeout=25, opener=None, headers=None):
    hdr = {"User-Agent": ua, "Accept": "*/*"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, headers=hdr)
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def nse_archive(url):
    last = None
    for ua in (NSE_UA, YAHOO_UA):
        try:
            return http_get(url, ua, timeout=60)
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(1)
    raise DataError("NSE did not answer for %s (%s). NSE blocks some networks; try again in a minute."
                    % (url.rsplit("/", 1)[-1], last))


class NSESession:
    """nseindia.com/api needs a cookie from one of its pages first, and a Referer."""

    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.hdr = {"User-Agent": NSE_UA, "Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9"}
        try:
            self.op.open(urllib.request.Request(NSE_COOKIE_PAGE, headers=self.hdr), timeout=25).read()
        except Exception:
            pass
        time.sleep(1)

    def get(self, path):
        req = urllib.request.Request("https://www.nseindia.com/" + path,
                                     headers=dict(self.hdr, Accept="application/json",
                                                  Referer="https://www.nseindia.com/"))
        with self.op.open(req, timeout=30) as r:
            doc = json.loads(r.read().decode("utf-8", "replace"))
        time.sleep(1)
        return doc


def yahoo_chart(ticker, rng="5y"):
    """Daily bars for one Yahoo ticker -> dict(t,o,h,l,c,v) with IST dates."""
    path = "/v8/finance/chart/%s?range=%s&interval=1d&events=split" % (urllib.request.quote(ticker), rng)
    last = "no answer"
    for attempt in range(3):
        for host in YAHOO_HOSTS:
            try:
                doc = json.loads(http_get(host + path, YAHOO_UA))
                res = (doc.get("chart") or {}).get("result")
                if not res:
                    err = (doc.get("chart") or {}).get("error") or {}
                    raise DataError(err.get("description") or "no data")
                return parse_chart(res[0])
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                e.close()
                if e.code == 404:
                    raise DataError("Yahoo has no symbol %s" % ticker)
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = str(getattr(e, "reason", e))
        time.sleep(3 * (attempt + 1))
    raise DataError("Yahoo answered %s for %s" % (last, ticker))


def parse_chart(r):
    ts = r.get("timestamp") or []
    q = (r.get("indicators") or {}).get("quote") or [{}]
    q = q[0]
    out = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    for i, t in enumerate(ts):
        o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        v = (q.get("volume") or [None] * len(ts))[i]
        if None in (o, h, l, c) or c <= 0:
            continue
        d = dt.datetime.fromtimestamp(t, IST).date().isoformat()
        if out["t"] and out["t"][-1] == d:   # Yahoo sometimes repeats the live bar
            out["h"][-1] = max(out["h"][-1], h, c)
            out["l"][-1] = min(out["l"][-1], l, c)
            out["c"][-1] = round(c, 4)
            out["v"][-1] = max(out["v"][-1], int(v or 0))
            continue
        out["t"].append(d)
        out["o"].append(round(o, 4))
        out["h"].append(round(max(h, o, c), 4))
        out["l"].append(round(min(l, o, c), 4))
        out["c"].append(round(c, 4))
        out["v"].append(int(v or 0))
    # Yahoo sometimes leaves the newest candle's close empty for hours after the close. Keep what it
    # has (and its last traded price) so fill_missing_bars can use it where NSE has no file (Sensex).
    if ts:
        i = len(ts) - 1
        o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        d = dt.datetime.fromtimestamp(ts[i], IST).date().isoformat()
        if c is None and None not in (o, h, l) and (not out["t"] or out["t"][-1] < d):
            meta = r.get("meta") or {}
            last, lt = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
            ok = last and lt and dt.datetime.fromtimestamp(lt, IST).date().isoformat() == d
            out["partial"] = {"t": d, "o": o, "h": h, "l": l,
                              "v": int((q.get("volume") or [None] * len(ts))[i] or 0),
                              "last": last if ok else None}
    return out


# ---------------------------------------------------------------- universe

def parse_index_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("Symbol"):
            out.append({"symbol": r["Symbol"], "name": r.get("Company Name", ""),
                        "sector": r.get("Industry", "") or "Other", "series": r.get("Series", "EQ"),
                        "isin": r.get("ISIN Code", "")})
    return out


def universe_key(which):
    w = (which or "").strip().lower().replace(" ", "")
    w = UNIVERSE_ALIASES.get(w, w)
    if w not in UNIVERSES:
        raise DataError("Unknown universe %r. Choose one of: %s" % (which, ", ".join(UNIVERSES)))
    return w


def _fno_symbols():
    text = nse_archive(NSE_FO_LOTS)
    out = []
    for line in text.splitlines()[1:]:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) > 1 and parts[1] and parts[1] != "SYMBOL" and not parts[0].upper().startswith("NIFTY") \
                and parts[1] not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"):
            out.append(parts[1])
    return out


def build_universe(p, which="nifty500", symbols=None):
    """Download an NSE index list (or the F&O list, or your own symbols) with sector, cap bucket and
    price band, and cache it as cache/universes/<name>.json."""
    w = universe_key(which)
    meta_all = {}
    if w in ("fno", "custom"):
        for s in parse_index_csv(nse_archive(NSE_INDEX_CSV % UNIVERSES["totalmarket"][0])):
            meta_all[s["symbol"]] = s
        wanted = _fno_symbols() if w == "fno" else [x.strip().upper() for x in (symbols or []) if x.strip()]
        if not wanted:
            raise DataError("A custom universe needs symbols, e.g. --symbols RELIANCE,TCS,INFY")
        stocks = [dict(meta_all.get(sym) or {"symbol": sym, "name": sym, "sector": "Other", "series": "EQ", "isin": ""})
                  for sym in wanted]
    else:
        stocks = parse_index_csv(nse_archive(NSE_INDEX_CSV % UNIVERSES[w][0]))
    # NSE keeps placeholder rows (e.g. "DUMMYHEG", ISIN "DUM...") in index lists around demergers
    stocks = [s for s in stocks if not s["symbol"].upper().startswith("DUMMY")
              and not (s.get("isin") or "").upper().startswith("DUM")]
    cap = {}
    for bucket, f in CAP_LISTS.items():
        try:
            for r in parse_index_csv(nse_archive(NSE_INDEX_CSV % f)):
                cap.setdefault(r["symbol"], bucket)
        except DataError:
            pass
    bands = {}
    try:
        for r in csv.DictReader(io.StringIO(nse_archive(NSE_SEC_LIST))):
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            if r.get("Series") == "EQ":
                bands[r["Symbol"]] = r.get("Band", "")
    except DataError:
        pass
    for s in stocks:
        s["cap"] = cap.get(s["symbol"], "micro")
        s["band"] = bands.get(s["symbol"], "")
    doc = {"index": w, "label": UNIVERSES[w][1], "fetched": now_ist().isoformat(timespec="minutes"),
           "count": len(stocks), "stocks": stocks, "bands_known": bool(bands)}
    if w == "custom":
        doc["symbols"] = [s["symbol"] for s in stocks]
    write_json(universe_file(p, w, symbols), doc)
    return doc


def universe_file(p, which, symbols=None):
    w = universe_key(which)
    if w == "custom":
        import hashlib
        tag = hashlib.sha1(",".join(sorted(x.upper() for x in symbols or [])).encode()).hexdigest()[:8]
        return os.path.join(p["universes"], "custom-%s.json" % tag)
    return os.path.join(p["universes"], w + ".json")


def load_universe(p, which="nifty500", symbols=None, max_age_days=None):
    doc = read_json(universe_file(p, which, symbols))
    if not doc:
        raise DataError("No %s list yet. Run: screener.py data --strategy NAME" % which)
    return doc


# ---------------------------------------------------------------- price cache

def price_file(p, key):
    safe = key.replace("^", "IDX_").replace("&", "_and_").replace("/", "_")
    return os.path.join(p["prices"], safe + ".json")


def ticker_for(symbol):
    if symbol in INDEX_TICKERS:
        return INDEX_TICKERS[symbol]
    if symbol.startswith("^") or symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return symbol + ".NS"


def _merge(old, new):
    """Append new bars to old. If the overlap disagrees (a split or bonus was applied), use new only."""
    if not old or not old.get("t"):
        return new, False
    pos = {d: i for i, d in enumerate(old["t"])}
    for i, d in enumerate(new["t"]):
        j = pos.get(d)
        if j is not None and i < len(new["t"]) - 1:
            a, b = old["c"][j], new["c"][i]
            if a and abs(a / b - 1) > 0.02:
                return None, True     # adjusted history changed -> refetch full range
    first_new = new["t"][0] if new["t"] else None
    keep = [i for i, d in enumerate(old["t"]) if first_new is None or d < first_new]
    out = {k: [old[k][i] for i in keep] + new[k] for k in "tohlcv"}
    return out, False


def fetch_one(p, key, years=5, force=False):
    path = price_file(p, key)
    old = None if force else read_json(path)
    tick = ticker_for(key)
    rng = "%dy" % years
    if old and old.get("t"):
        last = dt.date.fromisoformat(old["t"][-1])
        age = (now_ist().date() - last).days
        first = dt.date.fromisoformat(old["t"][0])
        span_years = (last - first).days / 365.25
        if span_years >= years - 0.1 and age <= 80:
            rng = "1mo" if age <= 20 else "6mo"
    new = yahoo_chart(tick, rng)
    if not new["t"]:
        raise DataError("no bars")
    if rng != "%dy" % years:
        merged, refetch = _merge(old, new)
        if refetch:
            new = yahoo_chart(tick, "%dy" % years)
            merged = new
    else:
        merged = new
    if new.get("partial"):
        merged["yahoo_partial"] = new["partial"]
    merged.pop("partial", None)
    merged["symbol"] = key
    merged["ticker"] = tick
    merged["fetched"] = now_ist().isoformat(timespec="minutes")
    write_json(path, merged)
    return len(merged["t"])


def update_prices(p, symbols, years=5, force=False, workers=6, log=print):
    done, failed = 0, {}
    t0 = time.time()

    def job(sym):
        return sym, fetch_one(p, sym, years, force)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(job, s): s for s in symbols}
        for n, f in enumerate(concurrent.futures.as_completed(futs), 1):
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001  one bad symbol never stops the run
                failed[futs[f]] = str(e)[:120]
            if n % 100 == 0:
                log("  %d / %d charts (%.0fs)" % (n, len(symbols), time.time() - t0))
    return done, failed


# ---------------------------------------------------------------- NSE end-of-day fallback

NSE_BHAV_ZIP = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip"
NSE_INDEX_CLOSE = "https://nsearchives.nseindia.com/content/indices/ind_close_all_%s.csv"
# price-file key -> the index's name in NSE's ind_close_all file
NSE_INDEX_NAMES = {
    "NIFTY500": "Nifty 500", "NIFTY50": "Nifty 50", "INDIAVIX": "India VIX",
    "^NSEBANK": "Nifty Bank", "NIFTY_FIN_SERVICE.NS": "Nifty Financial Services", "^CNXIT": "Nifty IT",
    "^CNXAUTO": "Nifty Auto", "^CNXPHARMA": "Nifty Pharma", "^CNXFMCG": "Nifty FMCG",
    "^CNXMETAL": "Nifty Metal", "^CNXENERGY": "Nifty Energy", "^CNXREALTY": "Nifty Realty",
    "^CNXPSUBANK": "Nifty PSU Bank", "^CNXINFRA": "Nifty Infrastructure",
    "^CNXCONSUM": "Nifty India Consumption", "^CNXPSE": "Nifty PSE", "^CNXMNC": "Nifty MNC",
}
EQ_SERIES = ("EQ", "BE", "BZ", "SM", "ST")


def _nse_bytes(url):
    """Bytes of an NSE archive file, or None when NSE has no such file (404)."""
    last = None
    for ua in (NSE_UA, YAHOO_UA):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 404:
                return None
            last = "HTTP %d" % e.code
        except (urllib.error.URLError, OSError) as e:
            last = e
        time.sleep(1)
    raise DataError("NSE did not answer for %s (%s)" % (url.rsplit("/", 1)[-1], last))


def nse_eod(p, day, now=None):
    """NSE's official end-of-day bars for one date, cached in cache/nse/eod/:
    {"stocks": {SYM: [o, h, l, c, v]}, "indices": {name: [o, h, l, c]}}, or None when NSE has
    no file for that date (a holiday, or not published yet: usually after 6 PM IST)."""
    now = now or now_ist()
    path = os.path.join(p["nse"], "eod", day.strftime("%Y%m%d") + ".json")
    doc = read_json(path)
    if doc is not None:
        return doc or None
    raw = _nse_bytes(NSE_BHAV_ZIP % day.strftime("%Y%m%d"))
    if raw is None:
        if (now.date() - day).days >= 3:          # long past and still no file: not a session
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_json(path, {})
        return None
    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(raw))
    text = zf.read([n for n in zf.namelist() if n.lower().endswith(".csv")][0]).decode("utf-8", "replace")
    stocks, rank = {}, {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("FinInstrmTp") != "STK" or r.get("SctySrs") not in EQ_SERIES:
            continue
        sym, pri = r["TckrSymb"], EQ_SERIES.index(r["SctySrs"])
        if sym in rank and rank[sym] <= pri:
            continue
        try:
            stocks[sym] = [float(r["OpnPric"]), float(r["HghPric"]), float(r["LwPric"]),
                           float(r["ClsPric"]), int(float(r["TtlTradgVol"] or 0))]
            rank[sym] = pri
        except (KeyError, ValueError):
            continue
    indices = {}
    raw = _nse_bytes(NSE_INDEX_CLOSE % day.strftime("%d%m%Y"))
    if raw:
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))):
            try:
                c = float(r["Closing Index Value"])
            except (KeyError, ValueError):
                continue
            num = lambda x: float(x) if x not in ("-", "", None) else c   # noqa: E731
            indices[r["Index Name"].strip()] = [num(r.get("Open Index Value")), num(r.get("High Index Value")),
                                                num(r.get("Low Index Value")), c]
    doc = {"stocks": stocks, "indices": indices}
    if stocks:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json(path, doc)
    return doc


def fill_missing_bars(p, keys, days=10, now=None, log=print):
    """Yahoo sometimes has no close for the latest session (the candle comes back with open, high
    and low but an empty close) or misses a day. For each chart behind, add the missing sessions of
    the last `days` days from NSE's official bhavcopy and index closing file. Sensex is not in NSE's
    files, so it uses Yahoo's last traded value for that day. Returns {date: charts filled}."""
    now = now or now_ist()
    docs = {}
    for k in keys:
        d = read_json(price_file(p, k))
        if d and d.get("t"):
            docs[k] = d
    if not docs:
        return {}
    today = now.date()
    cands = [today - dt.timedelta(days=i) for i in range(days, -1, -1)]
    cands = [d for d in cands if d.weekday() < 5 and (d < today or (now.hour, now.minute) >= (15, 45))]
    filled, changed, waiting = {}, set(), []
    for day in cands:
        ds = day.isoformat()
        need = [k for k, d in docs.items() if d["t"][-1] < ds]
        if not need:
            continue
        partial_here = [k for k in need if (docs[k].get("yahoo_partial") or {}).get("t") == ds]
        try:
            eod = nse_eod(p, day, now)
        except DataError as e:
            log("  NSE end-of-day file for %s: %s" % (ds, e))
            eod = None
        if eod is None and not partial_here:
            continue                               # not a session (or nobody has it)
        if eod is None:
            waiting.append(ds)
        n = 0
        for k in need:
            d = docs[k]
            bar = None
            if eod and k in NSE_INDEX_NAMES and NSE_INDEX_NAMES[k] in eod["indices"]:
                bar, src = eod["indices"][NSE_INDEX_NAMES[k]] + [0], "NSE index close file"
            elif eod and k.replace("_and_", "&") in eod["stocks"]:
                bar, src = eod["stocks"][k.replace("_and_", "&")], "NSE bhavcopy"
            elif k == "SENSEX":
                yp = d.get("yahoo_partial") or {}
                if yp.get("t") == ds and yp.get("last"):
                    c = yp["last"]
                    bar, src = [yp["o"], max(yp["h"], c), min(yp["l"], c), c, yp.get("v", 0)], "Yahoo last price"
            if not bar:
                continue
            o, h, l, c, v = bar
            d["t"].append(ds)
            d["o"].append(round(o, 4))
            d["h"].append(round(max(h, o, c), 4))
            d["l"].append(round(min(l, o, c), 4))
            d["c"].append(round(c, 4))
            d["v"].append(int(v))
            d.setdefault("filled", {})[ds] = src
            changed.add(k)
            n += 1
        if n:
            filled[ds] = n
    for k in changed:
        write_json(price_file(p, k), docs[k])
    if waiting:
        log("  Yahoo has no close yet for %s and NSE's file is not out (usually after 6 PM IST): "
            "run data again later." % ", ".join(waiting))
    return filled


def load_prices(p, key):
    return read_json(price_file(p, key))


def completed_bars(doc, asof=None, now=None):
    """Drop a bar that is still forming (today before 15:45 IST) and bars after the as-of date."""
    if not doc:
        return doc
    now = now or now_ist()
    cut = len(doc["t"])
    if asof:
        while cut and doc["t"][cut - 1] > asof:
            cut -= 1
    today = now.date().isoformat()
    if cut and doc["t"][cut - 1] == today and (now.hour, now.minute) < (15, 45):
        cut -= 1
    if cut == len(doc["t"]):
        return doc
    out = {k: doc[k][:cut] for k in "tohlcv"}
    out.update({k: doc[k] for k in doc if k not in "tohlcv"})
    return out


# ---------------------------------------------------------------- NSE surveillance and calendar

def update_nse_extras(p, log=print):
    """ASM + GSM lists and board meetings for the next 45 days. Best effort: failure is reported, not fatal."""
    status = {}
    try:
        s = NSESession()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    today = now_ist().date()
    try:
        asm = s.get("api/reportASM")
        rows = []
        for part in ("longterm", "shortterm"):
            for r in ((asm.get(part) or {}).get("data") or []):
                rows.append({"symbol": r.get("symbol"), "stage": r.get("asmSurvIndicator"),
                             "desc": r.get("survDesc"), "list": part})
        write_json(os.path.join(p["nse"], "asm.json"), {"fetched": today.isoformat(), "rows": rows})
        status["asm"] = len(rows)
    except Exception as e:  # noqa: BLE001
        status["asm_error"] = str(e)[:100]
    try:
        gsm = s.get("api/reportGSM")
        rows = [{"symbol": r.get("symbol"), "stage": r.get("gsmStage"), "desc": r.get("survDesc")}
                for r in (gsm if isinstance(gsm, list) else gsm.get("data", []))]
        write_json(os.path.join(p["nse"], "gsm.json"), {"fetched": today.isoformat(), "rows": rows})
        status["gsm"] = len(rows)
    except Exception as e:  # noqa: BLE001
        status["gsm_error"] = str(e)[:100]
    try:
        a, b = today.strftime("%d-%m-%Y"), (today + dt.timedelta(days=45)).strftime("%d-%m-%Y")
        ev = s.get("api/event-calendar?index=equities&from_date=%s&to_date=%s" % (a, b))
        rows = []
        for r in (ev if isinstance(ev, list) else []):
            try:
                d = dt.datetime.strptime(r.get("date", ""), "%d-%b-%Y").date().isoformat()
            except ValueError:
                continue
            rows.append({"symbol": r.get("symbol"), "date": d, "purpose": r.get("purpose", "")})
        write_json(os.path.join(p["nse"], "events.json"), {"fetched": today.isoformat(), "rows": rows})
        status["events"] = len(rows)
    except Exception as e:  # noqa: BLE001
        status["events_error"] = str(e)[:100]
    return status


def load_nse_extras(p):
    asm = read_json(os.path.join(p["nse"], "asm.json")) or {}
    gsm = read_json(os.path.join(p["nse"], "gsm.json")) or {}
    ev = read_json(os.path.join(p["nse"], "events.json")) or {}
    surv = {}
    for r in asm.get("rows", []):
        surv[r["symbol"]] = "ASM %s (%s)" % (r.get("stage") or "", "long term" if r.get("list") == "longterm" else "short term")
    for r in gsm.get("rows", []):
        surv[r["symbol"]] = "GSM stage %s" % (r.get("stage") or "")
    events = {}
    for r in ev.get("rows", []):
        events.setdefault(r["symbol"], []).append(r)
    return {"surveillance": surv, "surv_fetched": asm.get("fetched") or gsm.get("fetched"),
            "events": events, "events_fetched": ev.get("fetched")}


# ---------------------------------------------------------------- intraday bars (5m, 15m, 1h)

# timeframe -> (Yahoo interval, Yahoo range, minutes). 2h is built from 1h, 1w from 1d.
INTRADAY = {"5m": ("5m", "60d", 5), "15m": ("15m", "60d", 15), "1h": ("60m", "730d", 60)}
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "2h": 120, "1d": 375, "1w": 1875}
TIMEFRAMES = ("5m", "15m", "1h", "2h", "1d", "1w")
TF_ALIASES = {"5min": "5m", "5": "5m", "15min": "15m", "15": "15m", "60m": "1h", "60min": "1h", "1hr": "1h",
              "hourly": "1h", "120m": "2h", "2hr": "2h", "d": "1d", "day": "1d", "daily": "1d", "1day": "1d",
              "w": "1w", "week": "1w", "weekly": "1w", "1week": "1w"}
SESSION_OPEN, SESSION_CLOSE = 9 * 60 + 15, 15 * 60 + 30


def tf_key(tf):
    t = (tf or "1d").strip().lower()
    t = TF_ALIASES.get(t, t)
    if t not in TIMEFRAMES:
        raise DataError("Unknown timeframe %r. Choose one of: %s" % (tf, ", ".join(TIMEFRAMES)))
    return t


def intraday_file(p, tf, key):
    d = os.path.join(p["intraday"], tf)
    os.makedirs(d, exist_ok=True)
    safe = key.replace("^", "IDX_").replace("&", "_and_").replace("/", "_")
    return os.path.join(d, safe + ".json")


def yahoo_intraday(ticker, interval, rng):
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.request.quote(ticker), rng, interval)
    last = "no answer"
    for attempt in range(3):
        for host in YAHOO_HOSTS:
            try:
                doc = json.loads(http_get(host + path, YAHOO_UA))
                res = (doc.get("chart") or {}).get("result")
                if not res:
                    err = (doc.get("chart") or {}).get("error") or {}
                    raise DataError(err.get("description") or "no data")
                r = res[0]
                ts = r.get("timestamp") or []
                q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
                out = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
                for i, t in enumerate(ts):
                    o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
                    v = (q.get("volume") or [None] * len(ts))[i]
                    if None in (o, h, l, c) or c <= 0:
                        continue
                    tt = dt.datetime.fromtimestamp(t, IST)
                    mins = tt.hour * 60 + tt.minute
                    if mins < SESSION_OPEN or mins >= SESSION_CLOSE:
                        continue
                    out["t"].append(tt.strftime("%Y-%m-%d %H:%M"))
                    out["o"].append(round(o, 4))
                    out["h"].append(round(max(h, o, c), 4))
                    out["l"].append(round(min(l, o, c), 4))
                    out["c"].append(round(c, 4))
                    out["v"].append(int(v or 0))
                return out
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                e.close()
                if e.code == 404:
                    raise DataError("Yahoo has no symbol %s" % ticker)
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = str(getattr(e, "reason", e))
        time.sleep(3 * (attempt + 1))
    raise DataError("Yahoo answered %s for %s" % (last, ticker))


def merge_bars(old, new):
    """Keep old bars before the first new bar, then the new ones. If the overlap disagrees by more than
    2% (a split or bonus re-based the history), keep only the new bars."""
    if not old or not old.get("t"):
        return new
    if not new.get("t"):
        return old
    pos = {d: i for i, d in enumerate(old["t"])}
    for i, d in enumerate(new["t"][:-1]):
        j = pos.get(d)
        if j is not None and old["c"][j] and abs(old["c"][j] / new["c"][i] - 1) > 0.02:
            return new
    first = new["t"][0]
    keep = [i for i, d in enumerate(old["t"]) if d < first]
    return {k: [old[k][i] for i in keep] + new[k] for k in "tohlcv"}


def fetch_intraday_one(p, key, tf):
    interval, rng, _ = INTRADAY[tf]
    path = intraday_file(p, tf, key)
    old = read_json(path)
    new = yahoo_intraday(ticker_for(key), interval, rng)
    if not new["t"]:
        raise DataError("no bars")
    merged = merge_bars(old, new)
    merged.update({"symbol": key, "tf": tf, "fetched": now_ist().isoformat(timespec="minutes"),
                   "source": (old or {}).get("source", "yahoo") if old and old.get("source") == "kite" else "yahoo"})
    write_json(path, merged)
    return len(merged["t"])


def update_intraday(p, symbols, tf, workers=6, log=print):
    done, failed = 0, {}
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_intraday_one, p, s, tf): s for s in symbols}
        for n, f in enumerate(concurrent.futures.as_completed(futs), 1):
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                failed[futs[f]] = str(e)[:120]
            if n % 100 == 0:
                log("  %d / %d %s charts (%.0fs)" % (n, len(symbols), tf, time.time() - t0))
    return done, failed


def load_intraday(p, tf, key):
    return read_json(intraday_file(p, tf, key))


def import_bars(p, key, tf, file_path):
    """Merge candles exported from Zerodha Kite (get_historical_data) into the cache. Accepts a JSON list
    of {date, open, high, low, close, volume} or [date, o, h, l, c, v] rows, or {"candles": [...]}."""
    tf = tf_key(tf)
    raw = read_json(file_path)
    if raw is None:
        raise DataError("Cannot read %s as JSON" % file_path)
    rows = raw.get("candles") or raw.get("data") or raw if isinstance(raw, dict) else raw
    if isinstance(rows, dict):
        rows = rows.get("candles", [])
    out = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    for r in rows:
        if isinstance(r, dict):
            d, o, h, l, c, v = (r.get(k) for k in ("date", "open", "high", "low", "close", "volume"))
        else:
            d, o, h, l, c, v = (list(r) + [0])[:6]
        s = str(d).replace("T", " ")
        if tf in ("1d", "1w"):
            t = s[:10]
        else:
            t = s[:16]
            mins = int(t[11:13]) * 60 + int(t[14:16])
            if mins < SESSION_OPEN or mins >= SESSION_CLOSE:
                continue
        out["t"].append(t)
        for k, x in zip("ohlc", (o, h, l, c)):
            out[k].append(round(float(x), 4))
        out["v"].append(int(v or 0))
    order = sorted(range(len(out["t"])), key=lambda i: out["t"][i])
    out = {k: [out[k][i] for i in order] for k in "tohlcv"}
    if tf == "1d":
        path = price_file(p, key)
    elif tf in INTRADAY:
        path = intraday_file(p, tf, key)
    else:
        raise DataError("Import 5m, 15m, 1h or 1d bars (2h and 1w are built from them)")
    old = read_json(path)
    # imported (Kite) bars are the reference: Yahoo bars in the same span are replaced
    merged = merge_bars(old, out) if old else out
    if old and old.get("t") and out["t"] and old["t"][-1] > out["t"][-1]:
        tail = [i for i, d in enumerate(old["t"]) if d > out["t"][-1]]
        merged = {k: merged[k] + [old[k][i] for i in tail] for k in "tohlcv"}
    merged.update({"symbol": key, "tf": tf, "source": "kite", "fetched": now_ist().isoformat(timespec="minutes")})
    write_json(path, merged)
    return len(out["t"]), len(merged["t"])


def completed_intraday(doc, tf_minutes, asof=None, now=None):
    """Drop bars still forming (bar start + length after now) and bars after the as-of date/time."""
    if not doc or not doc.get("t"):
        return doc
    now = now or now_ist()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    cut = len(doc["t"])

    def end_of(t):
        mins = int(t[11:13]) * 60 + int(t[14:16]) + tf_minutes
        mins = min(mins, SESSION_CLOSE)
        return "%s %02d:%02d" % (t[:10], mins // 60, mins % 60)
    while cut and end_of(doc["t"][cut - 1]) > stamp:
        cut -= 1
    if asof:
        lim = asof if len(asof) > 10 else asof + " 23:59"
        while cut and doc["t"][cut - 1] > lim:
            cut -= 1
    if cut == len(doc["t"]):
        return doc
    out = {k: doc[k][:cut] for k in "tohlcv"}
    out.update({k: doc[k] for k in doc if k not in "tohlcv"})
    return out
