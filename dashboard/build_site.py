#!/usr/bin/env python3
"""
Build the private dashboard into dashboard/dist/ from jobs.db, applications/ logs and tailored resumes.

    build_site.py            # writes dist/index.html, dist/data.json, dist/resumes/<id>.pdf

The site holds personal data (resumes, phone, answers). serve.py shows it on this computer only.
"""
import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = HERE / "dist"
CFG = tomllib.loads((ROOT / "config.toml").read_text())


def funnel(scope):
    out = subprocess.run([str(ROOT / "fetch_jobs.py"), "funnel", "--json", "--scope", scope],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def section(text, heading):
    """Body of a '## heading' section in a markdown file, or ''."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    return m.group(1).strip() if m else ""


QA_SPLIT = re.compile(r"^(.+?(?:\?(?:\s*\([^)]*\))?:?|:))\s+(.+)$")


def parse_answers(log):
    """Structured view of a log's '## Questions and answers' section for the hover card.

    Logs are free-form, so this is best-effort: bullets become {q, a} pairs (several '; '-joined pairs per bullet are
    split), a non-bullet lead line ("Same form as #4") becomes `basis`, and '### ' subsections (essays) become
    {q: heading, a: body, long: True}. Anything unparsable is kept as {q: '', a: line} so nothing is dropped.
    """
    body = section(log, "Questions and answers")
    if not body:
        return None
    head, *subs = re.split(r"^###\s+", body, flags=re.M)
    basis, items = "", []
    for line in head.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("- "):
            basis = (basis + " " + line).strip()
            continue
        parts = [p.strip() for p in line[2:].split("; ")]
        if not all(QA_SPLIT.match(p) for p in parts):
            parts = [line[2:].strip()]
        for p in parts:
            m = QA_SPLIT.match(p)
            q, a = (m.group(1).rstrip(":").strip(), m.group(2).strip()) if m else ("", p)
            items.append(dict(q=q, a=a, empty=a.lower().startswith(("left empty", "left blank"))))
    for s in subs:
        title, _, text = s.partition("\n")
        text = re.sub(r"^>\s?", "", text.strip(), flags=re.M)
        items.append(dict(q=title.strip(), a=text, long=True))
    return dict(basis=basis, items=items)


def notes_by_person(text):
    """'### Name' -> note, from a '## Notes by person' section (person-specific openers with a sourced hook)."""
    body = section(text, "Notes by person")
    return {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"^###\s+(.+?)\n(.*?)(?=^###\s|\Z)", body, re.M | re.S)}


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "resumes").mkdir(parents=True)
    con = sqlite3.connect(ROOT / "jobs.db")
    con.row_factory = sqlite3.Row
    since = (dt.datetime.now() - dt.timedelta(hours=26)).isoformat(timespec="seconds")
    apps_dir = ROOT / "applications"
    rows = con.execute("""SELECT * FROM jobs WHERE status IN ('submitted','needs_user','filled','skipped')
                          ORDER BY COALESCE(submitted_at, updated_at) DESC""").fetchall()
    apps = []
    for r in rows:
        logs = sorted(p for p in apps_dir.glob(f"{r['id']}-*.md") if not p.name.endswith("-outreach.md"))
        outreach = apps_dir / f"{r['id']}-outreach.md"
        tailored = next((ROOT / "resumes" / "tailored").glob(f"{r['id']}-*/*_Resume.pdf"), None)
        resume = None
        if tailored and r["applied_by"] != "user":
            shutil.copy(tailored, DIST / "resumes" / f"{r['id']}.pdf")
            resume = f"resumes/{r['id']}.pdf"
        o = outreach.read_text() if outreach.exists() else ""
        apps.append(dict(
            id=r["id"], company=r["company"], title=r["title"], location=r["location"], url=r["url"],
            status=r["status"], outcome=r["outcome"] or "", reached=r["reached"] or "", applied_by=r["applied_by"] or "",
            source=r["source"], category=r["category"], score=r["score"], note=r["note"] or "",
            submitted_at=r["submitted_at"] or "", updated_at=r["updated_at"] or "",
            recent=bool((r["updated_at"] or "") >= since), resume=resume,
            log="\n\n".join(p.read_text() for p in logs), answers=next((x for x in map(parse_answers, (p.read_text() for p in logs)) if x), None),
            log_outcome=next((t for t in (section(p.read_text(), "Outcome") for p in logs) if t), ""),
            blockers=next((t for t in (section(p.read_text(), "Blockers") for p in logs) if t), ""), description=(r["description"] or "")[:5000],
            outreach=dict(who=section(o, "Who to look for"), note=section(o, "Note"), by_person=notes_by_person(o), why=section(o, "Why this note"),
                          people=[dict(zip(("name", "title", "url", "alum"), [x.strip() for x in l.strip("- ").split("|")]))
                                  for l in section(o, "People").splitlines() if l.count("|") >= 2]) if o else None))
    radar = []
    for p in sorted((ROOT / "applications" / "radar").glob("*.md"), reverse=True):
        t = p.read_text()
        head = t.splitlines()[0] if t else ""
        line = next((l.strip("- ").strip() for l in section(t, "Person").splitlines() if l.count("|") >= 3), "")
        f = [x.strip() for x in line.split("|")] if line else []
        radar.append(dict(date=p.stem, company=(f[2] if f else head.split("—")[-1].strip()),
                          person=dict(zip(("name", "title", "company", "url", "alum"), f)) if f else None,
                          note=section(t, "Note"), why=section(t, "Why this person")))
    # Approve tab: jobs the agent prepared (status ready) or you approved, with the plan in applications/<id>-plan.json
    approve = []
    (DIST / "files").mkdir(exist_ok=True)
    for r in con.execute("SELECT * FROM jobs WHERE status IN ('ready','approved','self') ORDER BY status DESC, score DESC"):
        plan_p = apps_dir / f"{r['id']}-plan.json"
        plan = json.loads(plan_p.read_text()) if plan_p.exists() else {}
        files = []
        tailored = next((ROOT / "resumes" / "tailored").glob(f"{r['id']}-*/*_Resume.pdf"), None)
        if tailored:
            shutil.copy(tailored, DIST / "resumes" / f"{r['id']}.pdf")
            files.append(dict(label="Resume PDF ↗", href=f"resumes/{r['id']}.pdf"))
        for f in plan.get("extra_files", []):
            src = ROOT / f["path"]
            if src.exists():
                shutil.copy(src, DIST / "files" / f"{r['id']}-{src.name}")
                files.append(dict(label=f["label"] + " ↗", href=f"files/{r['id']}-{src.name}"))
        approve.append(dict(id=r["id"], company=r["company"], title=r["title"], location=r["location"], url=r["url"], status=r["status"],
                            why=plan.get("why", ""), deadline=plan.get("deadline", ""), answers=plan.get("answers", []),
                            needs=plan.get("needs", []), files=files))
    queue = [dict(id=r["id"], company=r["company"], title=r["title"], location=r["location"], url=r["url"], score=r["score"])
             for r in con.execute("SELECT * FROM jobs WHERE status='new' ORDER BY score DESC, company LIMIT 25")]
    prof = {}
    if (ROOT / "profile.toml").exists():
        prof = tomllib.loads((ROOT / "profile.toml").read_text())
    data = dict(me=dict(first=prof.get("identity", {}).get("first_name", ""), school=prof.get("education", {}).get("school", "")),
                generated=dt.datetime.now().isoformat(timespec="minutes"), dry_run=CFG["submit"]["dry_run"],
                cap=CFG["submit"]["max_auto_submits_per_day"], applications=apps, approve=approve, queue=queue, radar=radar,
                funnel_all=funnel("all"), funnel_pipeline=funnel("pipeline"))
    (DIST / "data.json").write_text(json.dumps(data))
    shutil.copy(HERE / "index.html", DIST / "index.html")
    print(f"built {DIST}: {len(apps)} applications, {sum(1 for a in apps if a['resume'])} resumes, "
          f"{sum(1 for a in apps if a['outreach'])} outreach drafts")



if __name__ == "__main__":
    main()
