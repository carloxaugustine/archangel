#!/usr/bin/env python3
"""
Build a tailored one-page resume from bullet ids in facts.toml, using Jake's template.

    build_resume.py SPEC.json            # writes ../resumes/tailored/<job>-<company>/<Your_Name>_Resume.pdf
    build_resume.py --list               # print every entry, bullet and skills id

SPEC.json picks content by id only, so a resume can never say anything the bank doesn't:
{
  "job_id": 202, "company": "Example Co",
  "education": ["state-u"],
  "sections": [
    {"title": "Experience", "entries": [{"id": "acme", "bullets": ["acme-test", "acme-tools"]}]},
    {"title": "Projects",   "entries": [{"id": "robot", "bullets": ["robot-arm"]}]}
  ],
  "skills": ["sk-programming", "sk-design"]
}
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE.parent / "resumes" / "tailored"
FACTS_PATH = Path(os.environ.get("RESUME_FACTS") or HERE / "facts.toml")
if not FACTS_PATH.exists():
    sys.exit("build_resume: no resume-builder/facts.toml yet. Run /internship-setup in Claude Code first.")
FACTS = tomllib.loads(FACTS_PATH.read_text())
RULES = FACTS.get("rules", {})
PDF_NAME = re.sub(r"[^A-Za-z0-9]+", "_", FACTS["header"]["name"]).strip("_") + "_Resume"
TEMPLATE = (HERE / "jakes_template.tex").read_text()
PREAMBLE = TEMPLATE.split("\\begin{document}")[0]
# Tectonic runs XeTeX, which lacks pdfTeX's unicode-mapping primitives; guard them so the template compiles.
PREAMBLE = (PREAMBLE.replace("\\input{glyphtounicode}", "\\ifdefined\\pdfgentounicode\\input{glyphtounicode}\\fi")
            .replace("\\pdfgentounicode=1", "\\ifdefined\\pdfgentounicode\\pdfgentounicode=1\\fi"))
LAYOUT = r"""
% Jake's template restyled per guidelines.md: 11pt, 10.5pt bullets, one-line education rows.
\newcommand{\resumeEduLine}[4]{\unskip\vspace{2.5pt}\item\textbf{#1}, \textit{\small #2}\hfill\textit{\small #4}}
\renewcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0in, label={}, itemsep=0pt, topsep=0pt]}
\titlespacing*{\section}{0pt}{5pt}{6.5pt}
\renewcommand{\resumeItemListStart}{\begin{itemize}[itemsep=2.8pt,parsep=0pt,topsep=2pt,partopsep=0pt,leftmargin=0.16in]}
\renewcommand{\resumeItemListEnd}{\end{itemize}\vspace{1pt}}
"""
SECTION_GAP = ""
# Template bug: "{#1 \\vspace{-1.5pt}}" leaves a space that wraps onto an empty line when a bullet exactly fills its line.
PREAMBLE = PREAMBLE.replace("{#1 \\vspace{-1.5pt}}", "{#1\\unskip\\vspace{-1.5pt}}")
PREAMBLE = PREAMBLE.replace("{0.97\\textwidth}", "{\\textwidth}")  # use the full page width
# 10pt bullets read as a wall of text: 11pt base, 10.5pt bullets.
PREAMBLE = (PREAMBLE.replace("\\documentclass[letterpaper,10pt]{article}", "\\documentclass[letterpaper,11pt]{article}")
            .replace("\\item\\small{", "\\item\\fontsize{10.5}{12.1}\\selectfont{"))
# Spell out each acronym the first time it appears in the bullets (feedback: undefined jargon narrows the audience).
ACRONYMS = FACTS.get("acronyms", {})  # {"FEA": "finite element analysis"}


def expand_acronyms(texts):
    """texts in page order -> same texts with the first use of each acronym written as 'full name (ACR)'."""
    seen, out = set(), []
    for t in texts:
        for acr, full in ACRONYMS.items():
            if acr in seen:
                continue
            m = re.search(rf"\b{acr}(s?)\b", t)
            if not m:
                continue
            seen.add(acr)
            if f"({acr}" in t or full.lower() in t.lower():
                continue
            plural = m.group(1)
            pre = t[:m.start()]
            if pre.endswith("an ") and full[0].lower() not in "aeiou":  # "an ML" -> "a machine learning (ML)"
                pre = pre[:-3] + "a "
            elif pre.endswith("a ") and full[0].lower() in "aeiou":
                pre = pre[:-2] + "an "
            t = pre + f"{full}{plural} ({acr}{plural})" + t[m.end():]
        out.append(t)
    return out


def fail(msg):
    sys.exit(f"build_resume: {msg}")


def by_id(items, kind):
    out = {}
    for it in items:
        if it["id"] in out:
            fail(f"duplicate {kind} id {it['id']} in facts.toml")
        out[it["id"]] = it
    return out


EDU = by_id(FACTS["education"], "education")
ENTRIES = by_id(FACTS["entry"], "entry")
SKILLS = by_id(FACTS["skills"], "skills")


def header():
    """Header: phone, email, LinkedIn, website, citizenship (no location)."""
    h = FACTS["header"]
    items = [h.get("phone"), f"\\href{{mailto:{h['email']}}}{{{h['email']}}}" if h.get("email") else "",
             f"\\href{{https://{h['linkedin']}}}{{{h['linkedin']}}}" if h.get("linkedin") else "",
             f"\\href{{https://{h['website']}}}{{{h['website']}}}" if h.get("website") else "", h.get("citizenship")]
    items = [i for i in items if i]
    return ("\\begin{center}\n    {\\Huge \\scshape " + h["name"] + "} \\\\ \\vspace{1pt}\n    \\small "
            + " $|$ ".join(items) + "\n\\end{center}\n")


def education(ids):
    rows = []
    for i in ids:
        e = EDU.get(i) or fail(f"unknown education id {i}")
        if not e.get("confirmed"):
            fail(f"education {i} is not confirmed yet (confirmed = false in facts.toml)")
        gpa = f", GPA: {e['gpa']}" if e.get("gpa") else ""
        rows.append(f"    \\resumeEduLine{{{e['school']}}}{{{e['degree']}{gpa}}}{{{e.get('location', '')}}}{{{e['dates']}}}")
    return "\\section{Education}\n  \\begin{itemize}[leftmargin=0in, label={}, itemsep=0pt, topsep=2pt]\n" + "\n".join(rows) + "\n  \\end{itemize}\n"


def entry(spec, used, section="Experience"):
    e = ENTRIES.get(spec["id"]) or fail(f"unknown entry id {spec['id']}")
    if not e.get("confirmed"):
        fail(f"entry {spec['id']} is not confirmed yet (confirmed = false in facts.toml)")
    bank = {b["id"]: b["text"] for b in e["bullets"]}
    picked = spec.get("bullets") or fail(f"entry {spec['id']} lists no bullets")
    for b in picked:
        if b not in bank:
            fail(f"bullet {b} does not belong to entry {spec['id']}")
        if b in used:
            fail(f"bullet {b} used twice")
        used.add(b)
    if "tools" in e:  # project with a tools line instead of role/dates
        head = f"      \\resumeProjectHeading\n          {{{e['org']}}}{{{e['tools']}}}"
    else:
        loc = e.get("location", "")
        head = (f"    \\resumeSubheading\n      {{{e['org']}}}{{{loc}}}\n"
                f"      {{{e['role']}}}{{{e['dates']}}}")
    items = "\n".join(f"        \\resumeItem{{{bank[b]}}}" for b in picked)
    return f"{head}\n      \\resumeItemListStart\n{items}\n      \\resumeItemListEnd"


def skills(ids):
    if not 1 <= len(ids) <= 4:
        fail("pick one to four skills lines")
    lines = []
    for i in ids:
        s = SKILLS.get(i) or fail(f"unknown skills id {i}")
        lines.append(f"     \\textbf{{{s['label']}}}{{: {s['items']}}}")
    return ("\\section{" + RULES.get("skills_heading", "Skills") + "}\n \\begin{itemize}[leftmargin=0in, label={}]\n    {\\item{\n"
            + " \\\\\n".join(lines) + "\n    }}\n \\end{itemize}\n")


# Bullets that say the same thing in different words: at most one from each group on a resume.
VARIANTS = [set(g) for g in RULES.get("variants", [])]
REQUIRED_ENTRIES = set(RULES.get("required_entries", []))  # entries every resume must keep
MIN_BULLETS, MAX_BULLETS = RULES.get("min_bullets", 1), RULES.get("max_bullets", 5)


def check_latex(texts):
    """Bank text is LaTeX: an unescaped % silently eats the rest of the line, & # _ break the build."""
    bad = [t for t in texts if re.search(r"(?<!\\)[%&#_]", t)]
    return [f'unescaped LaTeX character in: "{t[:70]}"; write \\% \\& \\# \\_' for t in bad]


def check_spec(spec):
    """Resume rules that can be judged from the spec. Returns a list of problems."""
    problems = []
    titles = [s["title"] for s in spec["sections"]]
    if titles != sorted(titles, key=["Experience", "Projects"].index):
        problems.append("sections must run Education, Experience, Projects, Technical Skills")
    picked = set()
    for sec in spec["sections"]:
        for e in sec["entries"]:
            n = len(e.get("bullets", []))
            if not MIN_BULLETS <= n <= MAX_BULLETS:
                problems.append(f"{e['id']} has {n} bullet{'s' if n != 1 else ''}; every entry needs {MIN_BULLETS}-{MAX_BULLETS} "
                                f"(drop the entry instead of keeping one bullet)")
            picked.update(e.get("bullets", []))
    for g in VARIANTS:
        if len(g & picked) > 1:
            problems.append(f"{' and '.join(sorted(g & picked))} say the same thing; keep one")
    have = {e["id"] for sec in spec["sections"] for e in sec["entries"]}
    for must in REQUIRED_ENTRIES - have:
        problems.append(f"{must} is missing; every resume keeps every core entry (trim bullets instead)")
    if not 1 <= len(spec.get("skills", [])) <= 4:
        problems.append("pick one to four skills lines")
    return problems


def check_pdf(pdf, spec):
    """Rules that need the finished page: no one- or two-word wrapped lines, a full page, ATS-readable text."""
    problems = []
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    lines = [l.rstrip() for l in text.splitlines()]
    in_bullet = False
    for l in lines:
        t = l.strip()
        if not t:
            continue  # pdftotext puts a blank line between a bullet and its wrapped continuation
        if t.startswith(("•", "◦", "∙")):
            in_bullet = True
        elif in_bullet and l.startswith("  ") and "   " not in t:  # wrapped line of the bullet above
            if RULES.get("no_short_wraps") and len(t.split()) <= 4:  # optional: never spill 1-4 words onto the next line
                problems.append(f'a bullet wraps onto a line with only "{t}"; use the shorter variant or another bullet')
        else:
            in_bullet = False
    plain = " ".join(text.split())
    musts = [FACTS["header"]["name"], "Education", RULES.get("skills_heading", "Skills")] + [x["title"] for x in spec["sections"]]
    for must in musts:
        if must.lower() not in plain.lower():
            problems.append(f'"{must}" is not readable as text in the PDF (ATS check)')
    bbox = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True).stdout
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', bbox)]
    height = float((re.search(r'<page width="[\d.]+" height="([\d.]+)"', bbox) or [0, 792])[1])
    floor = height - 0.5 * 72  # page must be full: text ends within 0.1 in of the 0.4 in bottom margin
    if RULES.get("require_full_page") and ys and max(ys) < floor:
        problems.append(f"the page ends {(height - max(ys)) / 72:.2f} in above the bottom edge (max 0.50); add a relevant bullet or entry")
    return problems


def render(spec):
    used = set()
    body = [header(), education(spec.get("education", list(EDU))), SECTION_GAP]
    for sec in spec["sections"]:
        if sec["title"] not in ("Experience", "Projects"):
            fail(f"unknown section {sec['title']}")
        body.append(f"\\section{{{sec['title']}}}\n  \\resumeSubHeadingListStart\n"
                    + "\n".join(entry(e, used, sec["title"]) for e in sec["entries"]) + "\n  \\resumeSubHeadingListEnd\n")
        body.append(SECTION_GAP)
    body.append(skills(spec["skills"]))
    text = "\n".join(body)
    items = re.findall(r"^        \\resumeItem\{(.*)\}$", text, re.M)
    for old, new in zip(items, expand_acronyms(items)):
        if old != new:
            text = text.replace(f"\\resumeItem{{{old}}}", f"\\resumeItem{{{new}}}", 1)
    body = [text]
    # Margins per guidelines.md; width nudges below stay above the 0.4 in minimum.
    geo = "\\usepackage[letterpaper,top=0.4in,bottom=0.4in,left=0.45in,right=0.45in]{geometry}\n"
    return PREAMBLE + geo + LAYOUT + "\\begin{document}\n" + "\n".join(body) + "\n\\end{document}\n"


def pages(log):
    """Page count from the TeX log ("Output written on X.pdf (N pages)"); XeTeX compresses the PDF's page tree."""
    m = re.search(r"Output written on .*?\((\d+) pages?", log.read_text(errors="replace"))
    return int(m.group(1)) if m else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        for e in [*EDU.values(), *ENTRIES.values()]:
            flag = "" if e.get("confirmed") else "  [NOT CONFIRMED - unusable]"
            detail = " | ".join(str(e[k]) for k in ("degree", "role", "gpa", "location", "dates", "tools") if e.get(k))
            print(f"{e['id']}: {e.get('org', e.get('school'))} | {detail}{flag}")
            for b in e.get("bullets", []):
                print(f"    {b['id']}: {b['text']}")
        for s in SKILLS.values():
            print(f"skills {s['id']}: {s['label']}: {s['items']}")
        return
    if len(sys.argv) != 2:
        fail("usage: build_resume.py SPEC.json | --list")
    spec = json.loads(Path(sys.argv[1]).read_text())
    problems = check_spec(spec)
    problems += check_latex([b["text"] for e in ENTRIES.values() for b in e["bullets"]]
                            + [str(e.get(k, "")) for e in [*EDU.values(), *ENTRIES.values()] for k in ("org", "role", "school", "degree", "location", "dates", "tools")]
                            + [x["items"] for x in SKILLS.values()] + [x["label"] for x in SKILLS.values()])
    if problems:
        fail("resume breaks the resume rules:\n  - " + "\n  - ".join(problems))
    base = render(spec)
    slug = re.sub(r"[^a-z0-9]+", "-", str(spec.get("company", "job")).lower()).strip("-")
    out = OUT_ROOT / f"{spec.get('job_id', 'x')}-{slug}"
    best = None  # (problem list, tex) of the attempt with the fewest problems
    # Text width nudges (inches) that move where lines wrap; side margins stay within 0.42-0.56 in.
    for nudge in (0, 0.06, -0.06, 0.1, -0.12, -0.18):
        tex = base.replace("\\begin{document}", f"\\addtolength{{\\textwidth}}{{{nudge}in}}\\addtolength{{\\oddsidemargin}}"
                                                f"{{{-nudge / 2}in}}\\addtolength{{\\evensidemargin}}{{{-nudge / 2}in}}\n\\begin{{document}}", 1)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"{PDF_NAME}.tex"
            src.write_text(tex)
            r = subprocess.run(["tectonic", "--keep-logs", "--chatter", "minimal", str(src)], cwd=tmp, capture_output=True, text=True)
            pdf = Path(tmp) / f"{PDF_NAME}.pdf"
            if r.returncode or not pdf.exists():
                fail("LaTeX failed:\n" + (r.stderr or r.stdout)[-2000:])
            n = pages(Path(tmp) / f"{PDF_NAME}.log")
            problems = ([f"resume is {n} pages; drop a bullet or an entry"] if n != 1
                        else check_pdf(pdf, spec))
            if not problems:
                out.mkdir(parents=True, exist_ok=True)
                shutil.copy(pdf, out / pdf.name)
                (out / src.name).write_text(tex)
                (out / "spec.json").write_text(json.dumps(spec, indent=1))
                print(out / f"{PDF_NAME}.pdf")
                return
            if best is None or len(problems) < len(best):
                best = problems
    fail("resume breaks the resume rules at every layout tried; change the bullets or entries:\n  - "
         + "\n  - ".join(best))


if __name__ == "__main__":
    main()
