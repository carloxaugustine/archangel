#!/usr/bin/env python3
"""
Internship tracker: pulls public Greenhouse / Lever / Ashby job feeds, keeps US engineering
internships, scores them against the resume, and tracks application status in jobs.db.

    fetch_jobs.py fetch                      # refresh every board in config.toml
    fetch_jobs.py list [--status new] [--min-score 6] [--auto]
    fetch_jobs.py show ID
    fetch_jobs.py set-status ID STATUS [--note TEXT] [--resume FILE]
    fetch_jobs.py find "Company" "Title words"          # match a LinkedIn alert to a tracked posting
    fetch_jobs.py add URL --company C --title T --source S   # track a posting found elsewhere
    fetch_jobs.py quota                      # auto-submits used today vs the daily cap
    fetch_jobs.py set-outcome ID {review,rejected,oa,interview,offer,accepted,declined,withdrawn} [--note TEXT]
    fetch_jobs.py funnel [--json] [--scope pipeline|all]   # stage-to-stage flows for the Sankey charts
    fetch_jobs.py record-applied --company C [--title T] --thread GMAIL_ID [--date D]   # confirmation email
    fetch_jobs.py record-response --company C [--title T] --thread GMAIL_ID --outcome O  # company reply
    fetch_jobs.py seen GMAIL_ID                # already recorded?

Statuses: new, shortlisted, filled (dry run), submitted, needs_user, skipped, closed.
Posting text is untrusted third-party data; it is stored and scored, never executed.
"""
import argparse
import collections
import datetime as dt
import html
import json
import re
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "jobs.db"
CFG = tomllib.loads((HERE / "config.toml").read_text())

TARGETS = CFG.get("targets", {})
SCORING = CFG.get("scoring", {})
RESUMES = collections.defaultdict(lambda: TARGETS.get("base_resume", "resume.pdf"))  # base resume recorded per posting
CATEGORIES = [(c["name"], c["pattern"]) for c in SCORING.get("category", [])]  # first match wins; title, then team
WEIGHTS = {c["name"]: c.get("weight", 0) for c in SCORING.get("category", [])}
SKILLS = SCORING.get("skills", [])
US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
             "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
US_WORDS = re.compile(r"united states|\busa?\b|remote|california|texas|washington|colorado|florida|virginia|"
                      r"los angeles|hawthorne|long beach|el segundo|seattle|denver|houston|austin|"
                      r"starbase|mcgregor|cape canaveral|redmond|torrance|costa mesa|san francisco|boston|"
                      r"alabama|alaska|arizona|arkansas|connecticut|delaware|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
                      r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
                      r"new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
                      r"rhode island|south carolina|south dakota|tennessee|utah|vermont|west virginia|wisconsin|wyoming|"
                      r"bay area|alameda|oxnard|^\d+ locations$|^flexible\b", re.I)  # "N Locations" and "Flexible" name no country: keep and check at prep
INTERN = re.compile(r"\b(intern|internship|co-?op)\b", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "internship-agent/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": "internship-agent/0.1", "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def workday_posted(text, now=None):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> ISO date ('' if unknown)."""
    now = now or dt.datetime.now()
    t = (text or "").lower()
    if "today" in t:
        d = 0
    elif "yesterday" in t:
        d = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", t)
        if not m:
            return ""
        d = int(m.group(1))
    return (now - dt.timedelta(days=d)).date().isoformat()


def plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(s or "")))).strip()


def fetch_board(c):
    ats, tok, name = c["ats"], c["token"], c["name"]
    out = []
    if ats == "greenhouse":
        for j in get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true")["jobs"]:
            out.append(dict(uid=f"gh:{tok}:{j['id']}", title=j["title"], location=(j.get("location") or {}).get("name", ""),
                            team=", ".join(d["name"] for d in j.get("departments", [])), url=j["absolute_url"],
                            apply_url=j["absolute_url"], description=plain(j.get("content")),
                            posted=j.get("first_published") or j.get("updated_at", "")))
    elif ats == "lever":
        for j in get(f"https://api.lever.co/v0/postings/{tok}?mode=json"):
            cat = j.get("categories") or {}
            out.append(dict(uid=f"lv:{tok}:{j['id']}", title=j["text"], location=cat.get("location", ""),
                            team=" ".join(filter(None, [cat.get("department"), cat.get("team"), cat.get("commitment")])),
                            url=j["hostedUrl"], apply_url=j.get("applyUrl", j["hostedUrl"]),
                            description=plain(j.get("descriptionPlain") or j.get("description")),
                            posted=dt.datetime.fromtimestamp(j.get("createdAt", 0) / 1000).isoformat()))
    elif ats == "ashby":
        for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")["jobs"]:
            if j.get("isListed") is False:
                continue
            out.append(dict(uid=f"ab:{tok}:{j['id']}", title=j["title"], location=j.get("location", ""),
                            team=" ".join(filter(None, [j.get("department"), j.get("team"), j.get("employmentType")])),
                            url=j["jobUrl"], apply_url=j.get("applyUrl", j["jobUrl"]),
                            description=plain(j.get("descriptionPlain") or j.get("descriptionHtml")), posted=j.get("publishedAt", "")))
    elif ats == "gem":
        for j in get(f"https://api.gem.com/job_board/v0/{tok}/job_posts/"):
            out.append(dict(uid=f"gm:{tok}:{j['id']}", title=j.get("title", ""), location=(j.get("location") or {}).get("name", ""),
                            team=", ".join(d.get("name", "") for d in j.get("departments") or []), url=j["absolute_url"], apply_url=j["absolute_url"],
                            description=plain(j.get("content_plain") or j.get("content")), posted=j.get("first_published_at") or j.get("created_at", "")))
    elif ats == "phenom":
        # Phenom career sites (e.g. BAE Systems): token = host, e.g. "jobs.baesystems.com". Search "intern", 50 per page.
        offset, total = 0, None
        while total is None or offset < min(total, 500):
            body = {"lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results", "ddoKey": "refineSearch",
                    "sortBy": "", "subsearch": "", "from": offset, "jobs": True, "counts": False, "all_fields": [], "size": 50,
                    "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "siteType": "external", "keywords": "intern",
                    "global": True, "selected_fields": {}, "locationData": {}}
            r = post(f"https://{tok}/widgets", body)["refineSearch"]
            total = r.get("totalHits", 0)
            jobs = r.get("data", {}).get("jobs", [])
            for j in jobs:
                # postedDate is often a bulk refresh stamp on BAE's site; dateCreated is when the req was opened
                out.append(dict(uid=f"ph:{tok}:{j['jobId']}", title=j.get("title", ""), location=j.get("location", ""),
                                team=j.get("category", "") if isinstance(j.get("category"), str) else "", url=j.get("applyUrl", ""),
                                apply_url=j.get("applyUrl", ""), description=plain(j.get("descriptionTeaser")),
                                posted=j.get("dateCreated") or j.get("postedDate", "")))
            if not jobs:
                break
            offset += 50
    elif ats == "smartrecruiters":
        offset = 0
        while True:
            page = get(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=100&offset={offset}")
            for j in page.get("content", []):
                loc = j.get("location") or {}
                if not INTERN.search(j.get("name", "")):
                    continue  # details cost one request each, so only fetch them for internships
                det = get(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings/{j['id']}")
                sec = (det.get("jobAd") or {}).get("sections") or {}
                desc = " ".join(plain((sec.get(k) or {}).get("text")) for k in ("jobDescription", "qualifications", "additionalInformation"))
                url = det.get("postingUrl") or f"https://jobs.smartrecruiters.com/{tok}/{j['id']}"
                out.append(dict(uid=f"sr:{tok}:{j['id']}", title=j["name"], location=", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])),
                                team=(j.get("department") or {}).get("label", ""), url=url, apply_url=det.get("applyUrl", url),
                                description=desc, posted=j.get("releasedDate", "")))
            offset += 100
            if offset >= page.get("totalFound", 0):
                break
    elif ats == "workable":
        for j in get(f"https://apply.workable.com/api/v1/widget/accounts/{tok}?details=true").get("jobs", []):
            out.append(dict(uid=f"wk:{tok}:{j['shortcode']}", title=j["title"],
                            location=", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])),
                            team=j.get("department", ""), url=j.get("url") or j.get("shortlink", ""), apply_url=j.get("application_url") or j.get("url", ""),
                            description=plain(j.get("description")), posted=j.get("published_on") or j.get("created_at", "")))
    elif ats == "recruitee":
        for j in get(f"https://{tok}.recruitee.com/api/offers/").get("offers", []):
            out.append(dict(uid=f"rc:{tok}:{j['id']}", title=j["title"], location=j.get("location", ""), team=j.get("department") or "",
                            url=j.get("careers_url", ""), apply_url=j.get("careers_apply_url") or j.get("careers_url", ""),
                            description=plain((j.get("description") or "") + " " + (j.get("requirements") or "")), posted=j.get("published_at", "")))
    elif ats == "eightfold":
        # token "host|domain", e.g. "lockheedmartin.eightfold.ai|lockheedmartin.com"; the search API its robots.txt allows
        host, domain = tok.split("|")
        start, seen = 0, set()
        while start < 500:
            q = urllib.parse.urlencode({"domain": domain, "query": "intern", "location": "", "start": start, "sort_by": "timestamp"})
            if start:
                time.sleep(4)  # this API rate-limits fast paging
            try:
                data = get(f"https://{host}/api/pcsx/search?{q}").get("data") or {}
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                time.sleep(int(e.headers.get("Retry-After") or 60))  # back off as asked, once
                data = get(f"https://{host}/api/pcsx/search?{q}").get("data") or {}
            posts = data.get("positions") or []
            for j in posts:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                ts = j.get("postedTs") or j.get("creationTs")
                url = f"https://{host}{j.get('positionUrl', '')}"
                out.append(dict(uid=f"ef:{host}:{j['id']}", ref=str(j.get("displayJobId") or ""), title=j.get("name", ""), location="; ".join(j.get("standardizedLocations") or j.get("locations") or []),
                                team=j.get("department") or "", url=url, apply_url=url, description="",
                                posted=dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else ""))
            start += len(posts)
            if not posts or start >= (data.get("count") or 0):
                break
    elif ats == "workday":
        # token "tenant|wdN|site", e.g. "lockheedmartin|wd1|LMJobs"; search "intern" and page through results
        tenant, wd, site = tok.split("|")
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        seen = set()
        for query in ("intern", "internship", "co-op"):  # Workday search doesn't stem: "intern" misses "Internship" titles
            offset = 0
            while offset < 200:
                page = post(f"{base}/wday/cxs/{tenant}/{site}/jobs", {"limit": 20, "offset": offset, "searchText": query, "appliedFacets": {}})
                posts = page.get("jobPostings", [])
                for j in posts:
                    path = j.get("externalPath", "")
                    uid = f"wd:{tenant}:{path.rsplit('_', 1)[-1] or path}"
                    if uid in seen:
                        continue
                    seen.add(uid)
                    url = f"{base}/{site}{path}" if path else ""
                    out.append(dict(uid=uid, title=j.get("title", ""), location=j.get("locationsText", ""),
                                    team="", url=url, apply_url=url, description="", posted=workday_posted(j.get("postedOn"))))
                offset += 20
                if len(posts) < 20:
                    break
    for o in out:
        o.update(company=name, ats=ats)
    return out


def is_us(loc):
    if not loc or US_WORDS.search(loc):
        return True
    return any(re.search(rf",\s*{s}\b", loc) for s in US_STATES)


def classify(job):
    for field in (job["title"], job["team"]):
        for cat, pat in CATEGORIES:
            if re.search(pat, field, re.I):
                return cat
    return "other"


def score(job, cat):
    text = f"{job['title']} {job['description']}".lower()
    s = WEIGHTS.get(cat, 0)
    s += min(sum(bool(re.search(rf"(?<!\w){re.escape(k)}s?(?!\w)", text)) for k in SKILLS) // 2, 5)  # whole words: "cad" must not match "academic"
    if "engineer" in job["title"].lower():
        s += 1
    if TARGETS.get("year") and TARGETS["year"] in text:
        s += 2
    if TARGETS.get("stale_terms") and re.search(TARGETS["stale_terms"], text):
        s -= 4
    s += freshness(job.get("posted"))
    return s


def freshness(posted):
    """+3 for a posting from the last week, +2 within two weeks, +1 within a month: early applicants get read first."""
    try:
        when = dt.datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if when.tzinfo:
        when = when.astimezone().replace(tzinfo=None)
    age = (dt.datetime.now() - when).days
    return 3 if age <= 7 else 2 if age <= 14 else 1 if age <= 30 else 0


def keep(job):
    t = job["title"].lower()
    if not (INTERN.search(job["title"] or "") or re.search(r"\bintern", job.get("team") or "", re.I)):
        return False
    if any(x in t for x in CFG["filters"]["exclude_title"]):
        return False
    if CFG["filters"].get("us_only", True) and not is_us(job["location"]):
        return False
    return True


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY, uid TEXT UNIQUE, company TEXT, ats TEXT, title TEXT, location TEXT, team TEXT,
        url TEXT, apply_url TEXT, description TEXT, posted TEXT, category TEXT, score INTEGER, resume TEXT,
        source TEXT DEFAULT 'feed', status TEXT DEFAULT 'new', note TEXT DEFAULT '', first_seen TEXT,
        last_seen TEXT, submitted_at TEXT)""")
    if "updated_at" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:
        con.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")  # when status last changed
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    for c, ddl in (("applicants", "INTEGER"), ("deadline", "TEXT DEFAULT ''"), ("scout_note", "TEXT DEFAULT ''")):
        if c not in cols:  # scout fields: applicant count when a non-LinkedIn page shows it, closing date, why it was picked
            con.execute(f"ALTER TABLE jobs ADD COLUMN {c} {ddl}")
    if "outcome" not in cols:  # what the company did after a submitted application
        con.execute("ALTER TABLE jobs ADD COLUMN outcome TEXT DEFAULT ''")
        con.execute("ALTER TABLE jobs ADD COLUMN outcome_at TEXT")
        con.execute("ALTER TABLE jobs ADD COLUMN reached TEXT DEFAULT ''")  # stages passed, e.g. "oa,interview"
    if "applied_by" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN applied_by TEXT DEFAULT ''")    # 'agent' or 'user'
        con.execute("ALTER TABLE jobs ADD COLUMN mail_thread TEXT DEFAULT ''")   # Gmail thread of the confirmation
        con.execute("ALTER TABLE jobs ADD COLUMN response_threads TEXT DEFAULT ''")  # Gmail threads already recorded
    return con


def posting_key(url):
    """The ATS's own posting id from a URL (Greenhouse/Workday digits, Lever/Ashby UUIDs), for duplicate detection."""
    u = url or ""
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", u) or re.search(r"(?:jobs/|gh_jid=|token=|jobid=|_)(\d{6,})", u)
    return m.group(m.lastindex or 0) if m else ""


def same_posting(con, j):
    """id of an already-tracked row for the same posting (same ATS id, or same company + title + location), else None."""
    key = posting_key(j.get("url")) or posting_key(j.get("apply_url"))
    for r in con.execute("SELECT id, url, apply_url, title, location FROM jobs WHERE lower(company)=lower(?)", (j["company"],)):
        if j.get("ref") and (r["title"] or "").strip().endswith(f"({j['ref']})"):
            return r["id"]  # confirmation emails name the requisition, e.g. "Test Engineering Intern (12345)"
        if key and key in ((posting_key(r["url"]), posting_key(r["apply_url"]))):
            return r["id"]
        if r["title"].strip().lower() == j["title"].strip().lower() and (r["location"] or "")[:20] == (j["location"] or "")[:20]:
            return r["id"]
    return None


def cmd_fetch(a):
    con = db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    seen, errors, new = set(), [], 0
    with ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(fetch_board, c): c for c in CFG["company"]}
        for f, c in futs.items():
            try:
                jobs = f.result()
            except Exception as e:
                errors.append(f"{c['name']}: {e}")
                continue
            for j in filter(keep, jobs):
                cat = classify(j)
                seen.add(j["uid"])
                cur = con.execute("SELECT id FROM jobs WHERE uid=?", (j["uid"],)).fetchone()
                if cur:
                    con.execute("UPDATE jobs SET title=?, location=?, description=?, last_seen=?, category=?, score=?, "
                                "status=CASE WHEN status='closed' THEN 'new' ELSE status END WHERE uid=?",
                                (j["title"], j["location"], j["description"], now, cat, score(j, cat), j["uid"]))
                elif (dup := same_posting(con, j)):
                    con.execute("UPDATE jobs SET uid=?, last_seen=?, posted=CASE WHEN posted='' THEN ? ELSE posted END, "
                                "description=CASE WHEN description='' THEN ? ELSE description END WHERE id=?",
                                (j["uid"], now, j["posted"], j["description"], dup))
                else:
                    new += 1
                    con.execute("INSERT INTO jobs (uid, company, ats, title, location, team, url, apply_url, description, posted, "
                                "category, score, resume, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (j["uid"], j["company"], j["ats"], j["title"], j["location"], j["team"], j["url"], j["apply_url"],
                                 j["description"], j["posted"], cat, score(j, cat), RESUMES[cat], now, now))
    # postings that vanished from a feed that fetched fine are closed
    ok_prefixes = tuple(f"{ {'greenhouse': 'gh', 'lever': 'lv', 'ashby': 'ab', 'smartrecruiters': 'sr', 'workable': 'wk', 'recruitee': 'rc', 'workday': 'wd', 'eightfold': 'ef', 'gem': 'gm', 'phenom': 'ph'}[c['ats']] }:{c['token'].split('|')[0]}:".replace(" ", "")
                        for c in CFG["company"] if not any(e.startswith(c["name"] + ":") for e in errors))
    for r in con.execute("SELECT uid FROM jobs WHERE source='feed' AND status IN ('new','shortlisted')").fetchall():
        if r["uid"].startswith(ok_prefixes) and r["uid"] not in seen:
            con.execute("UPDATE jobs SET status='closed' WHERE uid=?", (r["uid"],))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('closed','skipped')").fetchone()[0]
    print(f"fetched {len(CFG['company']) - len(errors)} boards: {len(seen)} matching internships, {new} new, {total} open in tracker")
    for e in errors:
        print("  board error:", e)
    if not getattr(a, "no_lists", False):
        cmd_fetch_lists(a)


LISTS = [(r.split("/")[0].lower(), r) for r in TARGETS.get("github_lists", [])]  # community lists read through the gh CLI
ENG = re.compile(SCORING.get("list_include") or ".", re.I)      # titles to keep from lists
NOT_ENG = re.compile(SCORING.get("list_exclude") or "$^", re.I)  # titles to drop from lists


def fetch_lists():
    """Import matching internships from the GitHub lists in config.toml. Returns (added, skipped_dupes, errors)."""
    import shutil, subprocess
    if LISTS and not shutil.which("gh"):
        return 0, 0, ["gh CLI not installed; skipping GitHub lists"]
    con = db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    added, dupes, errors = 0, 0, []
    for tag, repo in LISTS:
        r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                            f"repos/{repo}/contents/.github/scripts/listings.json?ref=dev"], capture_output=True, text=True, timeout=120)
        if r.returncode:
            errors.append(f"{repo}: {r.stderr.strip()[:200]}"); continue
        for x in json.loads(r.stdout):
            if not (x.get("active") and x.get("is_visible", True)):
                continue
            summer = TARGETS.get("term_label", "") in (x.get("terms") or []) or (x.get("season") or "").lower() == TARGETS.get("season", "").lower()
            if not summer or not ENG.search(x.get("title", "")) or NOT_ENG.search(x.get("title", "")):
                continue
            locs = x.get("locations") or []
            j = dict(title=x["title"], team="", location="; ".join(locs), description="")
            if not keep(j) or (locs and not any(is_us(l) for l in locs)):
                continue
            uid = f"ls:{tag}:{x['id']}"
            if con.execute("SELECT 1 FROM jobs WHERE uid=? OR url=? OR (lower(company)=lower(?) AND lower(title)=lower(?))",
                           (uid, x["url"], x["company_name"], x["title"])).fetchone():
                dupes += 1; continue
            cat = classify(j)
            posted = dt.datetime.fromtimestamp(x["date_posted"]).isoformat(timespec="seconds") if x.get("date_posted") else ""
            ats = "greenhouse" if "greenhouse.io" in x["url"] else "lever" if "lever.co" in x["url"] else "ashby" if "ashbyhq.com" in x["url"] else "other"
            con.execute("INSERT INTO jobs (uid, company, ats, title, location, team, url, apply_url, description, posted, category, score, "
                        "resume, source, first_seen, last_seen, scout_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uid, x["company_name"], ats, x["title"], j["location"], "", x["url"], x["url"], "", posted, cat,
                         score(j, cat), RESUMES[cat], f"list:{tag}", now, now, f"from the {repo} list ({x.get('category') or x.get('season') or ''})"))
            added += 1
    con.commit()
    return added, dupes, errors


def cmd_fetch_lists(a):
    added, dupes, errors = fetch_lists()
    print(f"lists: {added} new engineering internships, {dupes} already tracked")
    for e in errors:
        print("  list error:", e)


def term(title):
    """Internship term named in a title ('summer', 'fall', ...), or '' when none is named."""
    m = re.search(r"\b(summer|fall|autumn|winter|spring)\b", title, re.I)
    return m.group(1).lower() if m else ""


_LOAD = None


def company_load(company):
    """Applications already sent or in flight at a company (submitted, approved, ready, filled)."""
    global _LOAD
    if _LOAD is None:
        _LOAD = {}
        for r in db().execute("SELECT lower(company) c, COUNT(*) n FROM jobs WHERE status IN "
                              "('submitted','approved','ready','filled') GROUP BY lower(company)"):
            _LOAD[r["c"]] = r["n"]
    return _LOAD.get((company or "").lower(), 0)


def focus_bar(company, base=None):
    """Fit score a new posting at this company needs: rises with each application already there."""
    s = CFG["submit"]
    return (s["min_score"] if base is None else base) + s.get("focus_step", 2) * company_load(company)


def auto_ok(r):
    s = CFG["submit"]
    t = term(r["title"])
    return (r["ats"] in s["auto_submit_ats"] and r["score"] >= focus_bar(r["company"])
            and (not t or t in s["auto_submit_terms"]))


def cmd_list(a):
    q, args = "SELECT * FROM jobs WHERE 1=1", []
    if a.status:
        q += " AND status=?"; args.append(a.status)
    else:
        q += " AND status NOT IN ('closed','skipped')"
    if a.min_score is not None:
        q += " AND score>=?"; args.append(a.min_score)
    rows = [r for r in db().execute(q + " ORDER BY score DESC, posted DESC, company", args) if not a.auto or auto_ok(r)]
    print(f"{'id':>4} {'sc':>3} {'auto':4} {'status':11} {'cat':10} {'company':16} {'location':24} title")
    for r in rows[: a.limit]:
        print(f"{r['id']:4d} {r['score']:3d} {'yes' if auto_ok(r) else '-':4} {r['status']:11} {r['category']:10} "
              f"{r['company'][:16]:16} {(r['location'] or '')[:24]:24} {r['title']}")
    print(f"({len(rows)} rows)")


def cmd_show(a):
    r = db().execute("SELECT * FROM jobs WHERE id=?", (a.id,)).fetchone()
    if not r:
        sys.exit(f"no job {a.id}")
    d = dict(r)
    d["auto_submit_eligible"] = auto_ok(r)
    d["resume_path"] = str(HERE / "resumes" / r["resume"])
    d["description"] = d["description"][: a.chars]
    print(json.dumps(d, indent=1))


def redeploy_site():
    """Rebuild the local dashboard after a tracker change. A failed rebuild never blocks the tracker."""
    import subprocess
    subprocess.run([str(HERE / "dashboard" / "build_site.py")], capture_output=True)


def cmd_set_status(a):
    con = db()
    sets, args = ["status=?", "updated_at=?"], [a.status, dt.datetime.now().isoformat(timespec="seconds")]
    if a.note:
        sets.append("note=?"); args.append(a.note)
    if a.resume:
        sets.append("resume=?"); args.append(a.resume)
    if a.status == "submitted":
        sets.append("submitted_at=?"); args.append(dt.datetime.now().isoformat(timespec="seconds"))
        sets.append("applied_by=CASE WHEN applied_by='' THEN 'agent' ELSE applied_by END")
    n = con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, a.id)).rowcount
    con.commit()
    print(f"job {a.id} -> {a.status}" if n else f"no job {a.id}")
    if n and a.status in ("submitted", "ready"):
        redeploy_site()


APPROVALS = HERE / "dashboard" / "approvals.json"


def cmd_pull_approvals(a):
    """Move Approve-tab clicks from dashboard/approvals.json into jobs.db: ready -> approved (answers saved per job) or skipped."""
    recs = json.loads(APPROVALS.read_text()) if APPROVALS.exists() else {}
    con = db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    moved = []
    for key, rec in list(recs.items()):
        jid = int(key)
        row = con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            continue
        if rec.get("action") == "approve" and row["status"] == "ready":
            (HERE / "applications" / f"{jid}-approval.json").write_text(json.dumps(rec, indent=1))
            con.execute("UPDATE jobs SET status='approved', updated_at=?, note=? WHERE id=?",
                        (now, f"approved on the dashboard {rec.get('at', '')[:16]}", jid))
            moved.append((jid, "approved"))
        elif rec.get("action") == "self" and row["status"] == "ready":
            (HERE / "applications" / f"{jid}-approval.json").write_text(json.dumps(rec, indent=1))
            con.execute("UPDATE jobs SET status='self', updated_at=?, note=? WHERE id=?",
                        (now, f"you chose to submit this yourself {rec.get('at', '')[:16]}", jid))
            moved.append((jid, "self"))
        elif rec.get("action") == "sent" and row["status"] in ("ready", "self"):
            con.execute("UPDATE jobs SET status='submitted', applied_by=?, submitted_at=?, updated_at=?, note=? WHERE id=?",
                        ("user", now, now, f"you submitted this yourself {rec.get('at', '')[:16]}", jid))
            moved.append((jid, "submitted"))
        elif rec.get("action") == "reject" and row["status"] == "ready":
            con.execute("UPDATE jobs SET status='skipped', updated_at=?, note=? WHERE id=?",
                        (now, f"rejected on the dashboard {rec.get('at', '')[:16]}", jid))
            moved.append((jid, "skipped"))
        con.commit()
        if row["status"] in ("ready", "approved", "submitted", "skipped", "self"):
            recs.pop(key)
    APPROVALS.write_text(json.dumps(recs, indent=1))
    for jid, st in moved:
        print(f"job {jid} -> {st}")
    if not moved:
        print("no new approvals")
    if moved and not a.no_deploy:
        redeploy_site()


HOME_AREA = TARGETS.get("home_area", "")  # regex of nearby places, for scout-rank --area home


def cmd_scout_rank(a):
    """Rank status=new postings. Order: posted within --days (newest first), then known applicants < 50, then
    closing soonest, then fit score. Postings with no posted date sort after dated ones (their age is unknown)."""
    now = dt.datetime.now()
    rows = db().execute("SELECT * FROM jobs WHERE status='new'").fetchall()

    def age_days(r):
        try:
            return (now - dt.datetime.fromisoformat((r["posted"] or "")[:19].replace("Z", ""))).total_seconds() / 86400
        except ValueError:
            return None

    out = []
    allowed = [t.lower() for t in CFG["submit"].get("auto_submit_terms", [])]
    off_term = re.compile(r"\b(" + "|".join(x for x in ("spring", "summer", "fall", "autumn", "winter") if x not in allowed) + r")\b", re.I)
    home = re.compile(HOME_AREA, re.I) if HOME_AREA else None
    for r in rows:
        age = age_days(r)
        if off_term.search(r["title"] or "") and not any(t in (r["title"] or "").lower() for t in allowed):
            continue  # outside the terms in config.toml
        has_desc = bool((r["description"] or "").strip())
        floor = CFG["submit"]["min_score"] if has_desc else 3  # list and Workday rows have no description to score
        if not has_desc and (not ENG.search(r["title"] or "") or NOT_ENG.search(r["title"] or "")):
            continue  # without a description, judge fit by the title alone
        if not re.search(r"intern|co-?op", r["title"] or "", re.I) or (r["score"] or 0) < focus_bar(r["company"], floor):
            continue  # internships only, and only ones that fit
        if getattr(a, "area", None) == "home" and not (home and home.search(r["location"] or "")):
            continue
        if age is not None and age > a.days:
            continue
        apps = r["applicants"]
        tier = (0 if age is not None else 1, 0 if (apps is not None and apps < 50) else (2 if apps is not None else 1))
        out.append((tier, age if age is not None else 99, r["deadline"] or "9999", -(r["score"] or 0), r))
    out.sort(key=lambda t: t[:4])
    items = [dict(id=r["id"], company=r["company"], title=r["title"], location=r["location"], url=r["url"],
                  posted=(r["posted"] or "")[:10], age_days=None if t[1] == 99 else round(t[1], 1), applicants=r["applicants"],
                  deadline=r["deadline"], score=r["score"], source=r["source"], note=r["scout_note"]) for *t, r in out[:a.limit]]
    if a.json:
        print(json.dumps(items, indent=1)); return
    print("| # | id | company | role | posted | age (d) | applicants | closes | fit | already applied there | source |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(items, 1):
        print(f"| {i} | {x['id']} | {x['company']} | [{x['title']}]({x['url']}) | {x['posted'] or '?'} | {x['age_days'] if x['age_days'] is not None else '?'} | "
              f"{x['applicants'] if x['applicants'] is not None else '?'} | {x['deadline'] or ''} | {x['score']} | {company_load(x['company'])} | {x['source']} |")


def cmd_find(a):
    words = [w for w in re.findall(r"[a-z0-9&+]+", a.title.lower()) if len(w) > 2 and w not in ("intern", "internship", "the", "and")]
    rows = db().execute("SELECT * FROM jobs WHERE lower(company) LIKE ?", (f"%{a.company.lower()}%",)).fetchall()
    hits = sorted(((sum(w in r["title"].lower() for w in words), r) for r in rows), key=lambda t: -t[0])
    for n, r in hits[:5]:
        if n:
            print(f"{r['id']:4d} match={n} {r['status']:11} {r['ats']:10} {r['title']}  {r['url']}")
    if not any(n for n, _ in hits[:5]):
        print("no match")


def cmd_add(a):
    con = db()
    dup = same_posting(con, dict(company=a.company, title=a.title, location=a.location or "", url=a.url, apply_url=a.url))
    if dup:
        print(f"already tracked as job {dup}; not added"); return
    now = dt.datetime.now().isoformat(timespec="seconds")
    j = dict(title=a.title, team="", description=a.description or "")
    cat = classify(j)
    ats = "greenhouse" if "greenhouse.io" in a.url else "lever" if "lever.co" in a.url else "ashby" if "ashbyhq.com" in a.url else "other"
    con.execute("INSERT OR IGNORE INTO jobs (uid, company, ats, title, location, team, url, apply_url, description, posted, category, "
                "score, resume, source, first_seen, last_seen, applicants, deadline, scout_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"ext:{a.url}", a.company, ats, a.title, a.location or "", "", a.url, a.url, j["description"], a.posted or "", cat,
                 score(j, cat), RESUMES[cat], a.source, now, now, a.applicants, a.deadline or "", a.note or ""))
    con.commit()
    print(con.execute("SELECT id, ats, category, score FROM jobs WHERE uid=?", (f"ext:{a.url}",)).fetchone()[:])


OUTCOMES = ["review", "rejected", "oa", "interview", "offer", "accepted", "declined", "withdrawn"]  # review = under review, no decision yet
ADVANCES = {"oa", "interview", "offer"}  # stages an application passes through on the way to a final answer


def apply_outcome(con, job_id, outcome, note=None, when=None):
    r = con.execute("SELECT status, reached FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        sys.exit(f"no job {job_id}")
    if r["status"] != "submitted":
        sys.exit(f"job {job_id} is '{r['status']}', not submitted; only submitted applications get outcomes")
    reached = [x for x in (r["reached"] or "").split(",") if x]
    if outcome in ADVANCES and outcome not in reached:
        reached.append(outcome)
    note = f"{outcome}: {note}" if note else None
    con.execute("UPDATE jobs SET outcome=?, outcome_at=?, reached=?" + (", note=?" if note else "") + " WHERE id=?",
                (outcome, when or dt.datetime.now().isoformat(timespec="seconds"), ",".join(reached), *([note] if note else []), job_id))
    return ",".join(reached)


def cmd_set_outcome(a):
    con = db()
    reached = apply_outcome(con, a.id, a.outcome, a.note)
    con.commit()
    print(f"job {a.id} outcome -> {a.outcome} (reached: {reached or 'none'})")


def thread_seen(con, thread):
    return con.execute("SELECT id FROM jobs WHERE mail_thread=? OR ','||response_threads||',' LIKE ?",
                       (thread, f"%,{thread},%")).fetchone()


def match(con, company, title, statuses):
    """Best tracked row for a company + role title (word overlap), among the given statuses."""
    words = {w for w in re.findall(r"[a-z0-9&+]+", (title or "").lower()) if len(w) > 2 and w not in ("intern", "internship", "the", "and", "for")}
    rows = con.execute(f"SELECT * FROM jobs WHERE lower(company) LIKE ? AND status IN ({','.join('?' * len(statuses))})",
                       (f"%{company.lower()}%", *statuses)).fetchall()
    best, best_n = None, 0
    for r in rows:
        n = len(words & set(re.findall(r"[a-z0-9&+]+", r["title"].lower())))
        if words and n >= max(2, (len(words) + 1) // 2) and n > best_n:
            best, best_n = r, n
    return best


def cmd_record_applied(a):
    """An application confirmation email: mark the tracked posting applied, or add the application."""
    con = db()
    if thread_seen(con, a.thread):
        return print(f"thread {a.thread} already recorded")
    when = a.date or dt.datetime.now().isoformat(timespec="seconds")
    r = match(con, a.company, a.title, ("submitted", "new", "shortlisted", "filled", "needs_user", "skipped", "closed"))
    if r:
        by = r["applied_by"] or ("agent" if r["status"] == "submitted" else "user")
        con.execute("UPDATE jobs SET status='submitted', applied_by=?, mail_thread=?, "
                    "submitted_at=COALESCE(submitted_at, ?), updated_at=? WHERE id=?", (by, a.thread, when, when, r["id"]))
        con.commit()
        print(f"job {r['id']} confirmed applied by {by}: {r['company']} / {r['title']}")
        return redeploy_site()
    con.execute("INSERT INTO jobs (uid, company, ats, title, location, team, url, apply_url, description, posted, category, score, "
                "resume, source, status, first_seen, last_seen, submitted_at, updated_at, applied_by, mail_thread) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"mail:{a.thread}", a.company, "other", a.title or "(role not named in email)", "", "", a.url or "", a.url or "",
                 "", "", classify({"title": a.title or "", "team": ""}), 0, "", "email", "submitted", when, when, when, when,
                 "user", a.thread))
    con.commit()
    print(f"added: {a.company} / {a.title or '(role not named)'} applied by you on {when[:10]}")
    redeploy_site()


def cmd_record_response(a):
    """A company reply (assessment, interview, offer, rejection) about a submitted application."""
    con = db()
    if thread_seen(con, a.thread):
        return print(f"thread {a.thread} already recorded")
    r = match(con, a.company, a.title, ("submitted",)) if a.title else None
    if not r:  # no role named: the most recent submitted application at that company still waiting on an answer
        r = con.execute("SELECT * FROM jobs WHERE lower(company) LIKE ? AND status='submitted' "
                        "ORDER BY (outcome='' OR outcome IN ('oa','interview','offer')) DESC, submitted_at DESC",
                        (f"%{a.company.lower()}%",)).fetchone()
    if not r:
        sys.exit(f"no submitted application at {a.company} to attach this reply to; record the confirmation first")
    reached = apply_outcome(con, r["id"], a.outcome, a.note, a.date)
    con.execute("UPDATE jobs SET response_threads=trim(response_threads||','||?, ',') WHERE id=?", (a.thread, r["id"]))
    con.commit()
    print(f"job {r['id']} {r['company']} / {r['title']}: {a.outcome} (reached: {reached or 'none'})")


def cmd_funnel(a):
    """Flows between application stages, for a Sankey chart: [{from, to, count}].
    --scope pipeline: postings the agent found -> applied -> outcomes. --scope all: every application, by who sent it."""
    names = {"by_user": "You", "by_agent": "Agent", "found": "Found", "Applied": "Applied", "Needs you": "Needs you", "Not applied yet": "Not applied yet",
             "Skipped": "Skipped", "Closed": "Closed", "submitted": "Applied", "none": "No reply yet", "rejected": "Rejected", "oa": "Online assessment",
             "interview": "Interview", "offer": "Offer", "accepted": "Accepted", "declined": "Declined",
             "withdrawn": "Withdrew", "review": "Under review", "pending": "No decision yet"}
    found = {"submitted": "Applied", "needs_user": "Needs you", "new": "Not applied yet", "shortlisted": "Not applied yet",
             "filled": "Not applied yet", "skipped": "Skipped", "closed": "Closed"}
    flows = {}
    if a.scope == "pipeline":
        for r in db().execute("SELECT status, COUNT(*) n FROM jobs WHERE source != 'email' GROUP BY status"):
            k = ("found", found.get(r["status"], "Not applied yet"))
            flows[k] = flows.get(k, 0) + r["n"]
    q = "SELECT outcome, reached, applied_by FROM jobs WHERE status='submitted'" + (" AND source != 'email'" if a.scope == "pipeline" else "")
    for r in db().execute(q):
        path = ["submitted"]
        if a.scope == "all":
            path = ["by_user" if r["applied_by"] == "user" else "by_agent"] + path + [x for x in ("oa", "interview", "offer") if x in (r["reached"] or "").split(",")]
        end = r["outcome"] or "none"
        if end == "review":
            path += ["review", "pending"]
            end = "pending"
        if end not in path:
            path.append(end)
        elif end == path[-1] and end in ADVANCES:
            path.append("none")  # still waiting on the next step
        for x, y in zip(path, path[1:]):
            flows[(x, y)] = flows.get((x, y), 0) + 1
    out = [{"from": names[x], "to": names[y], "count": n} for (x, y), n in flows.items()]
    print(json.dumps(out) if a.json else "\n".join(f"{f['from']:20} -> {f['to']:20} {f['count']}" for f in out) or "no postings tracked")


def cmd_quota(a):
    today = dt.date.today().isoformat()
    used = db().execute("SELECT COUNT(*) FROM jobs WHERE status='submitted' AND submitted_at LIKE ?", (today + "%",)).fetchone()[0]
    s = CFG["submit"]
    con = db()
    waiting = con.execute("SELECT COUNT(*) FROM jobs WHERE status='ready'").fetchone()[0]
    approved = con.execute("SELECT COUNT(*) FROM jobs WHERE status='approved'").fetchone()[0]
    print(json.dumps(dict(dry_run=s["dry_run"], require_approval=s.get("require_approval", True), submitted_today=used,
                          cap=s["max_auto_submits_per_day"], remaining=max(s["max_auto_submits_per_day"] - used, 0),
                          spacing_minutes=s.get("submit_spacing_minutes", 3), approved_waiting_to_submit=approved,
                          ready_waiting_for_you=waiting, min_score=s["min_score"],
                          auto_submit_essays=s["auto_submit_essays"], auto_submit_ats=s["auto_submit_ats"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("fetch"); p.add_argument("--no-lists", action="store_true", help="skip the GitHub internship lists"); p.set_defaults(f=cmd_fetch)
    sp.add_parser("fetch-lists", help="import matching internships from the GitHub lists in config.toml").set_defaults(f=cmd_fetch_lists)
    p = sp.add_parser("list"); p.add_argument("--status"); p.add_argument("--min-score", type=int)
    p.add_argument("--auto", action="store_true", help="only auto-submit-eligible rows"); p.add_argument("--limit", type=int, default=60)
    p.set_defaults(f=cmd_list)
    p = sp.add_parser("show"); p.add_argument("id", type=int); p.add_argument("--chars", type=int, default=3000); p.set_defaults(f=cmd_show)
    p = sp.add_parser("set-status"); p.add_argument("id", type=int)
    p.add_argument("status", choices=["new", "shortlisted", "ready", "approved", "filled", "submitted", "needs_user", "skipped", "closed"])
    p.add_argument("--note"); p.add_argument("--resume"); p.set_defaults(f=cmd_set_status)
    p = sp.add_parser("pull-approvals", help="read Approve-tab clicks from the local dashboard: ready -> approved / skipped")
    p.add_argument("--no-deploy", action="store_true"); p.set_defaults(f=cmd_pull_approvals)
    p = sp.add_parser("find"); p.add_argument("company"); p.add_argument("title"); p.set_defaults(f=cmd_find)
    p = sp.add_parser("add"); p.add_argument("url"); p.add_argument("--company", required=True); p.add_argument("--title", required=True)
    p.add_argument("--location"); p.add_argument("--description"); p.add_argument("--source", default="manual")
    p.add_argument("--posted", help="ISO date the posting went live, from the source itself"); p.add_argument("--applicants", type=int,
                   help="applicant count, only when the page shows one"); p.add_argument("--deadline"); p.add_argument("--note")
    p.set_defaults(f=cmd_add)
    p = sp.add_parser("scout-rank", help="rank open postings for the scout report: fresh (<=7 days) and low-applicant first")
    p.add_argument("--days", type=int, default=7); p.add_argument("--limit", type=int, default=40); p.add_argument("--json", action="store_true")
    p.add_argument("--area", choices=["home"], help="home = only places matching targets.home_area")
    p.set_defaults(f=cmd_scout_rank)
    sp.add_parser("quota").set_defaults(f=cmd_quota)
    p = sp.add_parser("record-applied", help="record an application confirmation email")
    p.add_argument("--company", required=True); p.add_argument("--title", default=""); p.add_argument("--thread", required=True)
    p.add_argument("--date"); p.add_argument("--url"); p.set_defaults(f=cmd_record_applied)
    p = sp.add_parser("record-response", help="record a company reply: oa, interview, offer, rejected, ...")
    p.add_argument("--company", required=True); p.add_argument("--title", default=""); p.add_argument("--thread", required=True)
    p.add_argument("--outcome", required=True, choices=OUTCOMES); p.add_argument("--date"); p.add_argument("--note")
    p.set_defaults(f=cmd_record_response)
    p = sp.add_parser("seen", help="is this Gmail thread already recorded?"); p.add_argument("thread")
    p.set_defaults(f=lambda a: print("yes" if thread_seen(db(), a.thread) else "no"))
    p = sp.add_parser("set-outcome", help="record a company's response to a submitted application")
    p.add_argument("id", type=int); p.add_argument("outcome", choices=OUTCOMES); p.add_argument("--note"); p.set_defaults(f=cmd_set_outcome)
    p = sp.add_parser("funnel", help="stage-to-stage flow counts for the Sankey chart"); p.add_argument("--json", action="store_true")
    p.add_argument("--scope", choices=["pipeline", "all"], default="pipeline")
    p.set_defaults(f=cmd_funnel)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
