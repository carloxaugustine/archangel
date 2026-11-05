# Archangel

An internship search agent that runs in Claude Code on your own computer. It finds postings, tailors a one-page resume for each job, prepares the application, and waits for your approval before anything is sent.

```
Job boards, GitHub lists, alert emails, scout
        ↓
Tracker: removes duplicates, filters, scores fit
        ↓
Prepare: tailored resume + drafted form answers
        ↓
Approve: you edit, approve or skip on a local dashboard
        ↓
Submit: Greenhouse and Lever go out from your browser. Other sites, you press Submit.
        ↓
Follow up: outreach drafts you rewrite and send yourself
```

## Setup

You need a Mac or Linux computer and a paid Claude plan.

**1. Install [Claude Code](https://docs.claude.com/en/docs/claude-code/overview).**

**2. Download this repo and run the setup script.** It checks for Python and the resume tools, and asks before installing anything.

```bash
git clone https://github.com/carloxaugustine/archangel.git
cd archangel
./setup.sh
```

**3. Put your resumes in the `inbox/` folder.** Include old versions, cover letters and project write-ups if you have them. If you have no resume, that's fine: the interview builds one from your answers.

**Optional, and worth it:** if you've used ChatGPT, Claude or another assistant for school or projects, paste the prompt from [`prompts/ai-context-export.md`](prompts/ai-context-export.md) into those chats and save each answer in `inbox/`. It recovers project details and numbers that never reached your resume. Setup asks you to confirm each one before using it, because AI memory can be wrong.

**4. Start the guided setup.**

```bash
claude
```

Then type `/internship-setup`. It reads your documents, asks about what's missing, builds a test resume, and tunes the search to your field. Allow 15 to 30 minutes.

## Daily use

| Do this | To |
|---|---|
| `/internship-agent` in Claude Code | find jobs and prepare applications, or fill the ones you approved |
| `./dashboard/serve.py` in a terminal | review and approve cards at http://127.0.0.1:8377 |
| `./install_schedule.sh 06:45` | run it every morning automatically (optional) |
| `./run_scout.sh` | search for newer, less competitive postings |

To fill forms, install the Claude in Chrome extension and start with `claude --chrome`. To track confirmations and replies, connect Gmail under Connectors in your claude.ai settings.

## What it won't do

- **Make things up.** Forms use only your profile and approved answers. Resumes can only use bullets from your own bank.
- **Send anything without approval.** New installs also start in dry-run mode: it fills forms and stops before Submit. Set `dry_run = false` in `config.toml` when you trust it.
- **Submit outside Greenhouse and Lever.** On Workday and other sites it can fill the form after you sign in, and you press Submit.
- **Automate LinkedIn, solve CAPTCHAs, enter passwords, or message anyone.** Outreach is drafts only.

## Your data stays yours

Your profile, resumes, applications and tracker stay in this folder, and `.gitignore` keeps them out of git. The dashboard runs only on your computer. Claude processes what you give it under your own Claude account, and job sites receive what you submit.

Before pushing a fork, run `git status` and confirm no personal files are listed.

## Be aware

- You're responsible for everything submitted in your name. Read each application, especially questions about work authorization or legal status.
- Some job sites restrict automated applications. Check the rules of the sites you use and decide for yourself.
- The starter company list leans toward aerospace, defense and hardware. Setup trims it for your field, but other fields will need more boards added.
- Applications alone rarely do it. Outreach to real people usually matters more.

## Troubleshooting

| Problem | Try |
|---|---|
| `/internship-setup` not found | Run `claude` from inside this folder |
| Resume won't build | Run `./resume-builder/build_resume.py <spec>` and read the message; it names the bullet or rule |
| No jobs, or the wrong jobs | Ask Claude to adjust `[scoring]` and `[[company]]` in `config.toml` for your field |
| A company is missing | `./probe_boards.py companyname` finds its board, then add it to `config.toml` |
| Browser tools unavailable | Start with `claude --chrome` and check the extension is on |

MIT licensed. Resume template by Jake Gutierrez.
