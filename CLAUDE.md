# Archangel

An internship search agent run through Claude Code. It finds postings, tailors a resume per job, prepares applications
for approval, submits approved Greenhouse and Lever applications, and drafts outreach the user sends themselves.

If `profile.toml` is missing or still empty, tell the user to run `./setup.sh` and then `/internship-setup`.

| Path | What it is |
|---|---|
| `archangel` | boot screen with live checks, then starts Claude Code |
| `.claude/skills/internship-setup/` | first-run interview |
| `.claude/skills/internship-agent/` | operating rules for every run; read before changing behavior |
| `.claude/agents/internship-scout.md` | finds fresh, low-competition postings |
| `config.toml` | submit settings, targets, scoring, company boards (private) |
| `profile.toml`, `answers.md` | the only facts that may go on a form (private) |
| `resume-builder/facts.toml` | resume bullet bank (private); `build_resume.py` only picks from it |
| `fetch_jobs.py`, `jobs.db` | feeds and tracker |
| `dashboard/` | local site: `serve.py` shows it at 127.0.0.1:8377 and saves Approve-tab clicks |
| `prompts/ai-context-export.md` | prompt users paste into other AI assistants to recover background |
| `applications/`, `resumes/tailored/` | per-job plans, logs, outreach and resumes (private) |

Hard rules: never invent a fact; nothing is submitted without the user's approval; never automate LinkedIn; never solve a
CAPTCHA or work around a login. Never commit anything `.gitignore` lists.
