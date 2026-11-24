"""News and catalysts for the screener's trade plans: NSE filings and India news headlines.

Ported from the marketfeed swing scanner (itself merged from the india-news skill): a two-week view per
trade plan, not "what is new since the last check". Standard library only.

    per candidate (PASS / REVIEW)   NSE corporate announcements, last 30 days (the company's own word,
                                    SEBI LODR Reg. 30) + Google News India headlines, last 14 days
    per sector in play              Google News India headlines, last 14 days

Sources (one request per feed per symbol, at most one request a second):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=..&from_date=..&to_date=..
        (needs NSE's cookies; when NSE blocks it, the public RSS of today's announcements is used)
    https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
    https://news.google.com/rss/search?q=..&hl=en-IN&gl=IN&ceid=IN:en

News is a catalyst, not a buy signal. Nothing here can move a stock into the funnel or upgrade a
check: a filing or headline that cuts the other way only turns the "News" check to REVIEW, so a
human reads it. Headlines are leads, not facts: open the filing or the story before acting.
"""

import datetime as _dt
import email.utils
import gzip
import hashlib
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
NSE_HOME = "https://www.nseindia.com/"
NSE_WARM = ["https://www.nseindia.com/companies-listing/corporate-filings-insider-trading", NSE_HOME]
NSE_ANN = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
           "&from_date=%s&to_date=%s")
NSE_RSS = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
NSE_ANN_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
NSE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/130 Safari/537.36")
TOOL_UA = "Mozilla/5.0 (compatible; marketfeed-screener/1.0)"
FILING_DAYS = 30
HEADLINE_DAYS = 14
MAX_HEADLINES = 10
MAX_FILINGS = 15
BLOCKED_HELP = ("NSE refused the request (it blocks scripts at times); run `screener.py news --strategy NAME` "
                "again in a few minutes.")

# NSE announcement subjects -> tags (checked against subject + text)
ANN_TAGS = [
    ("results", r"financial result|integrated filing.{0,3}financ|results? update|limited review"),
    ("board meeting", r"board meeting|outcome of (?:the )?board"),
    ("dividend/bonus/split", r"\bdividend\b|\bbonus\b|sub-?division|stock split|split of|record date|book closure"),
    ("buyback", r"\bbuy-?back\b"),
    ("deal", r"acquisition|amalgamation|merger|demerger|scheme of arrangement|sale or disposal|"
             r"divest|disinvest|joint venture|restructuring|\bstake\b|binding agreement"),
    ("order win", r"\border(?:s)? (?:win|received|inflow|bagged|worth)|bagging|award(?:ed|ing)? of|letter of award|"
                  r"\bLoA\b|receipt of (?:an? )?order|work order|purchase order"),
    ("rating", r"credit rating|\bCRISIL\b|\bICRA\b|\bCARE Ratings\b|India Ratings"),
    ("management", r"change in director|appointment|resignation|cessation|\bKMP\b|key managerial|"
                   r"change in management|managing director|\bCEO\b|\bCFO\b|company secretary|auditor"),
    ("regulatory", r"action\(s\) taken or orders passed|\bSEBI\b.{0,40}(?:order|notice|penalt)|"
                   r"show cause|penalt|\border(?:s)? passed\b|\bNCLT\b|\bCCI\b|regulator|GST demand|tax demand"),
    ("capital", r"allotment|preferential|\bQIP\b|rights issue|debenture|\bNCD\b|fund rais|"
                r"initial public offer|\bIPO\b|\bESOP|\bESOS|warrants"),
    ("insider/SAST", r"insider trading|takeover|\bSAST\b|regulation 31|\bpledge|encumbrance"),
    ("clarification", r"news verification|clarification"),
    ("meeting/call", r"analysts?/institutional|con\.? call|earnings call|investor meet|investor presentation"),
    ("risk", r"\bdefault\b|insolvency|\bIBC\b|\bfraud\b|forensic|\braid\b|search and seizure|"
             r"winding up|delay in (?:payment|servicing)|suspension of (?:trading|operations|production|plant|"
             r"manufactur|licen|business)|plant (?:shut|closure)|import alert|warning letter"),
]
# The kind of filing that gaps a stock: marked "!"
IMPORTANT_TAGS = {"results", "dividend/bonus/split", "buyback", "deal", "order win", "rating", "regulatory", "risk"}
ROUTINE = re.compile(r"loss of share certificate|duplicate share|copy of newspaper|newspaper publication|"
                     r"trading window|certificate under sebi|declaration of nav|"
                     r"compliance certificate|reg\.? ?74", re.I)

HEADLINE_TAGS = [
    ("results", r"\bQ[1-4]\b|\bresults?\b|\bearnings\b|\bnet profit\b|\bprofit (?:rises|falls|jumps|drops|up|down)|"
                r"\brevenue\b|\bPAT\b|\bEBITDA\b|\bguidance\b"),
    ("order win", r"\border(?:s)? (?:win|worth|from|inflow)|\bbags?\b.{0,30}\border|\bwins?\b.{0,30}\b(?:order|contract)\b|"
                  r"\bcontract worth\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\bbuy rating\b|\bsell rating\b|"
                r"\boverweight\b|\bunderweight\b|\boutperform\b|\bbrokerage[s]?\b|\bJefferies\b|\bCLSA\b|"
                r"\bMorgan Stanley\b|\bGoldman\b|\bBernstein\b|\bMacquarie\b|\bNomura\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\btakeover\b|\bdeal\b|\bstake\b|\bbuyout\b"),
    ("dividend/bonus/split", r"\bdividend\b|\bbonus\b|\bstock split\b|\bsplit\b|\brecord date\b|\bbuy-?back\b"),
    ("rating", r"\bcredit rating\b|\bCRISIL\b|\bICRA\b|\bMoody['’]?s\b|\bFitch\b|\bS&P\b"),
    ("management", r"\bCEO\b|\bCFO\b|\bMD\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint|\bsuccession\b"),
    ("regulatory", r"\bSEBI\b|\bNCLT\b|\bCCI\b|\bpenalty\b|\bshow[- ]cause\b|\bprobe\b|\binvestigation\b|"
                   r"\b[Cc]ourt\b|\b[Tt]ribunal\b|\bED\b|\b[Ii]ncome [Tt]ax\b|\bGST\b|\bban\b"),
    ("block/bulk deal", r"\bblock deal\b|\bbulk deal\b|\bOFS\b|\boffer for sale\b|\bstake sale\b|\bpromoters? (?:sell|buy)"),
    ("capital", r"\bIPO\b|\bQIP\b|\brights issue\b|\bfund ?rais|\bNCD\b|\bpreferential\b"),
    ("risk", r"\bdefault\b|\binsolvency\b|\bfraud\b|\braid\b|\bpledge\b|\bleak(?:ed|s)?\b|\bdowngrade\b"),
    ("policy", r"\bPLI\b|\bbudget\b|\bpolicy\b|\bgovernment\b|\bministry\b|\btariffs?\b|\bduty\b|\bsubsid"),
    ("macro", r"\bRBI\b|\brepo rate\b|\bMPC\b|\binflation\b|\bCPI\b|\bGDP\b|\bFIIs?\b|\bFPIs?\b|\bDIIs?\b|"
              r"\brupee\b|\bcrude\b"),
]

# Evergreen SEO pages (daily "prediction" levels, "stocks to watch in 2027", share price pages): not news
JUNK_HEADLINE = re.compile(r"prediction for \w+day|levels? for (?:mon|tues|wednes|thurs|fri)day|(?:share|stock) price(?: today| live)?$|"
                           r"(?:stock|investor|trader) analysis\b|stocks? to (?:watch|buy) in \d{4}|compared \d{4}$|"
                           r"\bvs\b.*\d{4}$|^what (?:is|are) |share price today|on NSE/BSE$|stock holdings$|52 week high streak|"
                           r"\bCAGR vs\b", re.I)

# Filings and headlines that cut against a long trade: the News check goes to REVIEW.
RED_FILING_TAGS = {"regulatory", "risk"}
CHECK_FILING_TAGS = {"rating", "capital", "deal", "clarification"}
RED_HEADLINE_TAGS = {"risk", "regulatory", "block/bulk deal"}

# NSE industry (the universe's sector) -> a Google News query for that sector's story
SECTOR_QUERY = {
    "Financial Services": '"bank stocks" OR "NBFC stocks" OR "Nifty Bank" OR "Nifty Financial"',
    "Capital Goods": '"capital goods stocks" OR "defence stocks" OR "order inflow" OR "capex"',
    "Healthcare": '"pharma stocks" OR "Nifty Pharma" OR "healthcare stocks" OR "hospital stocks"',
    "Automobile and Auto Components": '"auto stocks" OR "Nifty Auto" OR "auto sales" OR "auto ancillary"',
    "Fast Moving Consumer Goods": '"FMCG stocks" OR "Nifty FMCG" OR "consumption stocks"',
    "Chemicals": '"chemical stocks" OR "specialty chemicals" OR "agrochemical stocks"',
    "Information Technology": '"IT stocks" OR "Nifty IT" OR "IT services" OR "Indian IT"',
    "Consumer Services": '"retail stocks" OR "quick commerce" OR "hotel stocks" OR "consumer services stocks"',
    "Metals & Mining": '"metal stocks" OR "Nifty Metal" OR "steel prices" OR "aluminium prices"',
    "Power": '"power stocks" OR "power demand" OR "power sector" OR "renewable energy stocks"',
    "Oil Gas & Consumable Fuels": '"oil and gas stocks" OR "OMC stocks" OR "crude oil" OR "Nifty Oil"',
    "Consumer Durables": '"consumer durables stocks" OR "electronics manufacturing" OR "cable and wire stocks"',
    "Services": '"logistics stocks" OR "services sector" OR "shipping stocks"',
    "Realty": '"realty stocks" OR "Nifty Realty" OR "housing sales"',
    "Construction": '"infra stocks" OR "infrastructure stocks" OR "construction stocks"',
    "Construction Materials": '"cement stocks" OR "cement prices"',
    "Telecommunication": '"telecom stocks" OR "telecom tariff"',
    "Textiles": '"textile stocks" OR "textile exports"',
    "Media Entertainment & Publication": '"media stocks" OR "Nifty Media"',
}


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=_dt.timezone.utc)
    return utc.astimezone(IST).replace(tzinfo=None)


def parse_time(value):
    """RFC 822 (RSS), ISO 8601 or NSE's "21-Oct-2026 22:44:39" (IST) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(value, fmt).replace(tzinfo=IST).astimezone(_dt.timezone.utc)
        except ValueError:
            pass
    d = None
    if re.match(r"^[A-Z][a-z]{2}, ", value) or re.search(r"\b(GMT|UT|[+-]\d{4})$", value):
        try:
            d = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def ist_text(d):
    return to_ist(d).strftime("%Y-%m-%d %H:%M") if d else None


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Polite HTTP: one request a second, NSE through a cookie session, one retry."""

    def __init__(self, pause=1.0):
        self.pause = pause
        self._last = 0.0
        self._warm = False
        self.nse_blocked = False
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, url, headers):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        with self.opener.open(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data

    def warm_nse(self):
        if self._warm:
            return
        self._warm = True
        for page in NSE_WARM:
            try:
                self._open(page, {"User-Agent": NSE_UA, "Accept": "text/html,*/*",
                                  "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"})
                return
            except (urllib.error.URLError, OSError):
                continue

    def get(self, url, kind="plain"):
        """kind: 'nse' (JSON API, cookies), 'archive' (nsearchives), 'plain' (Google News)."""
        if kind == "nse":
            if self.nse_blocked:
                raise NewsError("NSE is refusing requests. " + BLOCKED_HELP)
            self.warm_nse()
            headers = {"User-Agent": NSE_UA, "Accept": "application/json, text/plain, */*", "Referer": NSE_HOME,
                       "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}
        elif kind == "archive":
            headers = {"User-Agent": NSE_UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
        else:
            headers = {"User-Agent": TOOL_UA, "Accept-Encoding": "gzip"}
        last = None
        for attempt in (1, 2):
            try:
                data = self._open(url, headers)
                if kind == "nse" and data[:1] not in (b"[", b"{"):
                    raise NewsError("NSE sent a web page, not data. " + BLOCKED_HELP)
                return data
            except urllib.error.HTTPError as exc:
                last = exc
                if kind == "archive" and attempt == 1:
                    headers = dict(headers, **{"User-Agent": TOOL_UA})
                    continue
                if exc.code in (401, 403) and kind == "nse":
                    self._warm = False
                    time.sleep(2)
                    self.warm_nse()
                    continue
                if exc.code == 429 or exc.code >= 500:
                    time.sleep(2)
                    continue
                break
            except (urllib.error.URLError, OSError) as exc:
                last = exc
                time.sleep(1)
        if isinstance(last, urllib.error.HTTPError):
            if kind == "nse" and last.code in (401, 403):
                self.nse_blocked = True
                raise NewsError("HTTP %s from NSE. %s" % (last.code, BLOCKED_HELP))
            raise NewsError("HTTP %s for %s" % (last.code, url.split("?")[0]))
        raise NewsError("could not reach %s: %s" % (url.split("?")[0], getattr(last, "reason", last)))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text.replace("''", "'")).strip()


def short_name(name):
    """'Cyient Ltd.' -> 'Cyient'; 'Reliance Industries Limited' -> 'Reliance Industries'."""
    n = re.sub(r"\s+", " ", (name or "")).strip()
    n = re.sub(r"(?i)[\s,.]+(?:limited|ltd)\.?$", "", n)
    return n.strip()


def norm_name(name):
    n = re.sub(r"(?i)\b(?:limited|ltd)\b\.?", "", name or "")
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def _filing(symbol, seq, subject, text, link, when, source):
    tags = [label for label, rx in ANN_TAGS if re.search(rx, "%s %s" % (subject, text), re.I)]
    routine = bool(ROUTINE.search(subject + " " + text))
    snippet = text if len(text) <= 220 else text[:217].rstrip() + "..."
    return {"id": "nse:%s" % seq, "kind": "filing", "source": source, "symbol": symbol,
            "subject": subject or "Announcement", "text": snippet, "link": link or NSE_ANN_PAGE,
            "time": when, "tags": tags, "routine": routine,
            "important": bool(set(tags) & IMPORTANT_TAGS) and not routine}


def parse_nse_announcements(data, symbol):
    try:
        rows = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE announcements for %s are not JSON. %s" % (symbol, BLOCKED_HELP))
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    out = []
    for r in rows:
        subject, text = clean(r.get("desc")), clean(r.get("attchmntText"))
        link = (r.get("attchmntFile") or "").strip()
        if link in ("-", "null"):
            link = ""
        seq = r.get("seq_id") or hashlib.sha1(("%s|%s|%s" % (symbol, r.get("an_dt"), subject))
                                              .encode("utf-8")).hexdigest()[:12]
        out.append(_filing(symbol, seq, subject, text, link, parse_time(r.get("an_dt") or r.get("sort_date")), "NSE"))
    return out


def parse_nse_rss(data, names):
    """Today's public NSE announcements RSS -> filings for {symbol: company name} (matched on the name)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("NSE announcements RSS is not valid XML: %s" % exc)
    by_name = {norm_name(n): s for s, n in names.items() if n}
    out = []
    for it in root.iter("item"):
        sym = by_name.get(norm_name(clean(_text(it, "title"))))
        if not sym:
            continue
        desc, subject = clean(_text(it, "description")), ""
        m = re.search(r"\|\s*SUBJECT:\s*(.*)$", desc)
        if m:
            subject, desc = m.group(1).strip(), desc[:m.start()].strip()
        link, when = _text(it, "link"), parse_time(_text(it, "pubDate"))
        seq = "rss-" + hashlib.sha1(("%s|%s|%s" % (sym, link, when)).encode("utf-8")).hexdigest()[:12]
        out.append(_filing(sym, seq, subject, desc, link, when, "NSE RSS"))
    return out


def parse_gnews(data, label):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("news RSS for %s is not valid XML: %s" % (label, exc))
    out = []
    for it in root.iter("item"):
        title, publisher = clean(_text(it, "title")), _text(it, "source")
        if publisher and title.endswith(" - " + publisher):
            title = title[:-(len(publisher) + 3)].rstrip()
        link = _text(it, "link")
        out.append({"id": "gn:" + (_text(it, "guid") or link), "kind": "headline", "source": "Google News",
                    "publisher": publisher, "title": title, "link": link, "time": parse_time(_text(it, "pubDate")),
                    "tags": [t for t, rx in HEADLINE_TAGS if re.search(rx, title)]})
    return out


def dedupe(items):
    seen, out = set(), []
    for it in items:
        keys = [it["id"], it.get("link") if it.get("link") != NSE_ANN_PAGE else "",
                "%s|%s" % (re.sub(r"\W+", " ", (it.get("title") or it.get("subject", "")).lower()).strip(),
                           it["time"].date() if it.get("time") else "")]
        if any(k and k in seen for k in keys):
            continue
        seen.update(k for k in keys if k)
        out.append(it)
    return out


# ---------------------------------------------------------------- windows

def window(asof, replay=False, now=None, bar_minutes=None):
    """(end, live) for a run whose last bar is `asof` ("YYYY-MM-DD" daily/weekly, or "YYYY-MM-DD HH:MM",
    the START of an intraday bar). A live run reaches up to now, so announcements made after the bar
    are included. An --asof replay stops at the end of that bar (end of the day for daily bars, the
    bar's close for intraday ones), so later news never leaks into it."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if len(asof) > 10:
        start = _dt.datetime.strptime(asof[:16], "%Y-%m-%d %H:%M")
        end_ = start + _dt.timedelta(minutes=bar_minutes or 5)
    else:
        end_ = _dt.datetime.strptime(asof[:10], "%Y-%m-%d") + _dt.timedelta(days=1)
    end_ = end_.replace(tzinfo=IST).astimezone(_dt.timezone.utc)
    if replay and end_ < now:
        return end_, False
    return now, True


def gnews_query(query, end, live, days):
    if live:
        return "%s when:%dd" % (query, days)
    end_ist = to_ist(end).date()
    return "%s after:%s before:%s" % (query, (end_ist - _dt.timedelta(days=days)).isoformat(),
                                      (end_ist + _dt.timedelta(days=1)).isoformat())


def _in_window(items, end, days):
    start = end - _dt.timedelta(days=days)
    ahead = end + _dt.timedelta(minutes=10)
    return [i for i in items if i.get("time") is None or start <= i["time"] <= ahead]


# ---------------------------------------------------------------- the checks

def headlines(fetcher, query, end, live, days=HEADLINE_DAYS, label=""):
    url = GNEWS % urllib.parse.quote_plus(gnews_query(query, end, live, days))
    items = _in_window(dedupe(parse_gnews(fetcher.get(url), label or query)), end, days)
    items = [i for i in items if not JUNK_HEADLINE.search(i["title"])]
    items.sort(key=lambda i: i["time"] or end, reverse=True)
    return items[:MAX_HEADLINES]


def stock_news(fetcher, symbol, name, end, live, filing_days=FILING_DAYS, headline_days=HEADLINE_DAYS):
    """{"filings": [...], "headlines": [...], "notes": [...]} for one stock."""
    notes, filings, heads = [], [], []
    filings_ok = heads_ok = False
    start_ist, end_ist = to_ist(end - _dt.timedelta(days=filing_days)), to_ist(end)
    try:
        url = NSE_ANN % (urllib.parse.quote(symbol, safe="-"), start_ist.strftime("%d-%m-%Y"), end_ist.strftime("%d-%m-%Y"))
        filings = parse_nse_announcements(fetcher.get(url, "nse"), symbol)
        filings_ok = True
    except NewsError as exc:
        notes.append("NSE announcements: %s" % exc)
        if live and name:
            try:
                filings = parse_nse_rss(fetcher.get(NSE_RSS, "archive"), {symbol: name})
                notes.append("used NSE's public RSS instead: it carries today's announcements only")
            except NewsError as exc2:
                notes.append("NSE announcements RSS: %s" % exc2)
    filings = [f for f in _in_window(dedupe(filings), end, filing_days) if not f["routine"]]
    filings.sort(key=lambda i: i["time"] or end, reverse=True)
    company = short_name(name)
    query = '"%s"' % company if company else '"%s" NSE share' % symbol
    try:
        heads = headlines(fetcher, query, end, live, headline_days, symbol)
        heads_ok = True
    except NewsError as exc:
        notes.append("headlines: %s" % exc)
    return {"symbol": symbol, "name": name, "filings": filings[:MAX_FILINGS], "headlines": heads,
            "filings_ok": filings_ok, "headlines_ok": heads_ok, "notes": notes}


def flags(sn, end):
    """What to read before acting, worst first: [(level, text)], level 'red' | 'check' | 'info'."""
    out, results_seen = [], False
    for f in sn["filings"]:
        day = to_ist(f["time"]).strftime("%d %b") if f.get("time") else "recent"
        tags = set(f["tags"])
        what = f["subject"]
        if f.get("text") and re.match(r"(?i)press release|general updates?|updates?|announcement", f["subject"]):
            m = re.search(r'titled\s+["\u201c]([^"\u201d]+)', f["text"])
            what = m.group(1) if m else re.sub(r"(?i)^.{0,80}?has informed the exchange (?:regarding|about) ", "", f["text"])
            what = what[:140]
        if "capital" in tags and re.search(r"ESOP|ESOS|ESPS|employee stock", f["subject"] + " " + f["text"], re.I) \
                and not re.search(r"preferential|QIP|rights issue|warrant", f["subject"] + " " + f["text"], re.I):
            tags.discard("capital")  # employee stock allotments are routine dilution
        if tags & RED_FILING_TAGS or ("insider/SAST" in tags and re.search(r"pledge|encumbrance", f["subject"] + f["text"], re.I)):
            out.append(("red", "%s filing on %s: %s" % ("/".join(sorted(tags & (RED_FILING_TAGS | {"insider/SAST"}))),
                                                         day, what)))
        elif "management" in tags and re.search(r"resignation|cessation", f["subject"] + " " + f["text"], re.I):
            out.append(("check", "Management exit filed on %s: %s. Who left, and why?" % (day, f["text"][:120] or f["subject"])))
        elif tags & CHECK_FILING_TAGS:
            out.append(("check", "%s filing on %s: %s" % ("/".join(sorted(tags & CHECK_FILING_TAGS)), day, what)))
        elif "results" in tags and not results_seen and f.get("time") and (end - f["time"]).days <= 10:
            results_seen = True
            out.append(("info", "Results filed on %s: the move may be a reaction to them; read the numbers "
                                "(one-off gains, guidance)" % day))
        elif "order win" in tags:
            out.append(("info", "Order filing on %s: %s" % (day, what)))
    for h in sn["headlines"]:
        bad = set(h["tags"]) & RED_HEADLINE_TAGS
        if bad:
            out.append(("check", "Headline (%s, %s): %s" % ("/".join(sorted(bad)), h.get("publisher") or "news", h["title"])))
    seen, uniq = set(), []
    for lv, tx in out:
        if tx not in seen:
            seen.add(tx)
            uniq.append((lv, tx))
    order = {"red": 0, "check": 1, "info": 2}
    uniq.sort(key=lambda x: order[x[0]])
    return uniq


def news_check(sn, fl):
    """The trade plan's 'News' check. It can only ask for a human read (REVIEW); good news never upgrades."""
    red = [t for lv, t in fl if lv == "red"]
    chk = [t for lv, t in fl if lv == "check"]
    if red:
        return "REVIEW", "Cuts the other way: %s. Read it before any order" % red[0] + \
            ("; %d more item(s) to read" % (len(red) + len(chk) - 1) if len(red) + len(chk) > 1 else "")
    if chk:
        return "REVIEW", "Read first: %s%s" % (chk[0], "; %d more" % (len(chk) - 1) if len(chk) > 1 else "")
    if not sn["filings_ok"] and not sn["headlines_ok"]:
        return "REVIEW", "News not checked (feeds did not answer): read the NSE announcements yourself"
    part = "%d NSE filing(s) in %d days, %d headline(s) in %d days" % (
        len(sn["filings"]), FILING_DAYS, len(sn["headlines"]), HEADLINE_DAYS)
    if not sn["filings_ok"]:
        part += " (NSE announcements not checked)"
    return "PASS", "No red flags: %s. News explains a move; it is not the reason to buy" % part


def attach_news(run, log=print, now=None, symbols=None, max_stocks=15):
    """Fetch filings + headlines for the run's PASS/REVIEW candidates and headlines for the sectors in
    play, add a 'News' check to each candidate, and keep everything under run['news']."""
    replay = run.get("replay", any(w.startswith("As-of replay") for w in run.get("warnings", [])))
    end, live = window(run["asof"], replay, now, run.get("bar_minutes"))
    fetcher = Fetcher()
    keep_notes = (run.get("news") or {}).get("notes", {})
    news = {"checked": ist_text(_dt.datetime.now(_dt.timezone.utc)) + " IST", "until": ist_text(end) + " IST",
            "live": live, "filing_days": FILING_DAYS, "headline_days": HEADLINE_DAYS,
            "stocks": {}, "sectors": {}, "notes": keep_notes, "errors": []}
    wanted = [c for c in run["candidates"] if c["status"] != "REJECT"]
    if symbols:
        wanted = [c for c in run["candidates"] if c["symbol"] in symbols]
    done = {}
    for c in wanted[:max_stocks]:
        sym = c["symbol"]
        if sym not in done:
            log("  news: %s" % sym)
            sn = stock_news(fetcher, sym, c.get("name"), end, live)
            fl = flags(sn, end)
            sn["flags"] = [{"level": lv, "text": tx} for lv, tx in fl]
            done[sym] = (sn, news_check(sn, fl))
            news["stocks"][sym] = _json_ready(sn)
        sn, (st, det) = done[sym]
        c["checks"] = [x for x in c["checks"] if x["check"] != "News"]
        c["checks"].append({"check": "News", "status": st, "detail": det})
        sts = {x["status"] for x in c["checks"]}
        c["status"] = "REJECT" if "REJECT" in sts else "REVIEW" if "REVIEW" in sts else "PASS"
    if len(wanted) > max_stocks:
        news["errors"].append("news checked for the first %d candidates only" % max_stocks)
    sectors = list(run.get("top_sectors") or [])
    for c in wanted:
        if c.get("sector") and c["sector"] not in sectors:
            sectors.append(c["sector"])
    for sec in sectors[:6]:
        q = "(%s) India" % SECTOR_QUERY.get(sec, '"%s stocks"' % sec)
        try:
            log("  news: sector %s" % sec)
            news["sectors"][sec] = [_item_json(h) for h in headlines(fetcher, q, end, live, HEADLINE_DAYS, sec)[:8]]
        except NewsError as exc:
            news["errors"].append("sector headlines for %s: %s" % (sec, exc))
    run["news"] = news
    order = {"PASS": 0, "REVIEW": 1, "REJECT": 2}
    def rs(x):
        v = x.get("rs_rank")
        return v if isinstance(v, (int, float)) and v == v else 0
    run["candidates"].sort(key=lambda x: (order[x["status"]], -((x.get("signal") or {}).get("score") or 0), -rs(x)))
    return run


def _item_json(it):
    d = dict(it)
    d["time_ist"] = ist_text(it.get("time"))
    d["time"] = it["time"].isoformat() if it.get("time") else None
    return d


def _json_ready(sn):
    d = dict(sn)
    d["filings"] = [_item_json(f) for f in sn["filings"]]
    d["headlines"] = [_item_json(h) for h in sn["headlines"]]
    return d
