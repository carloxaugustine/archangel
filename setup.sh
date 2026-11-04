#!/bin/bash
# Installs what the agent needs and creates your private settings files. Safe to run again.
set -u
cd "$(dirname "$0")" || exit 1
ok()   { printf '  \033[32mok\033[0m  %s\n' "$1"; }
warn() { printf '  \033[33m!!\033[0m  %s\n' "$1"; }
ask()  { read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[Yy] ]]; }
brew_install() {  # brew_install <brew package> <why>
  if command -v brew >/dev/null; then
    if ask "Install $1 with Homebrew ($2)?"; then brew install "$1"; fi
  else
    warn "Install $1 yourself ($2). On Debian or Ubuntu try: sudo apt install $1"
  fi
}
echo "Checking tools"

PY=""
for c in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys,tomllib; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  brew_install python@3.13 "the agent's scripts need Python 3.11 or newer"
  PY="$(command -v python3.13 || true)"
fi
if [ -z "$PY" ]; then warn "Python 3.11 or newer not found. Install it and run ./setup.sh again."; exit 1; fi
ok "Python: $PY"

command -v tectonic >/dev/null || brew_install tectonic "builds resume PDFs"
command -v pdftotext >/dev/null || brew_install poppler "checks resume PDFs"
command -v gh >/dev/null || brew_install gh "optional: reads GitHub internship lists"
for t in tectonic pdftotext; do
  if command -v "$t" >/dev/null; then ok "$t"; else warn "$t missing: resumes won't build until it's installed"; fi
done
if command -v gh >/dev/null; then ok "gh"; else warn "gh missing: GitHub internship lists will be skipped"; fi
if command -v claude >/dev/null; then ok "Claude Code"; else warn "Claude Code missing: see the README, step 1"; fi

echo "Creating your private files (ignored by git)"
for pair in config.example.toml:config.toml profile.example.toml:profile.toml answers.example.md:answers.md; do
  src="${pair%%:*}"; dst="${pair##*:}"
  if [ -e "$dst" ]; then ok "$dst already exists, left alone"; else cp "$src" "$dst"; ok "created $dst"; fi
done
[ -e scout/sources.md ] || printf '# Scout source ledger\nOne line per source: `source | last checked | fresh postings found | notes`\n\n' > scout/sources.md
[ -e scout/boards.md ] || printf '# Boards the scout found that are not in config.toml yet\n`company | ats | token | why it fits`\n\n' > scout/boards.md

# Scripts normally use "python3"; point them at the right one only if that one is too old.
if [ "$(command -v python3 || true)" != "$PY" ]; then
  for f in fetch_jobs.py resume-builder/build_resume.py dashboard/build_site.py dashboard/serve.py; do
    "$PY" - "$f" "$PY" <<'PYEOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); lines = p.read_text().split("\n"); lines[0] = "#!" + sys.argv[2]; p.write_text("\n".join(lines))
PYEOF
  done
fi
chmod +x fetch_jobs.py resume-builder/build_resume.py dashboard/build_site.py dashboard/serve.py run_daily.sh run_scout.sh install_schedule.sh

echo "Testing"
./fetch_jobs.py quota >/dev/null && ok "tracker runs" || warn "tracker failed: run ./fetch_jobs.py quota to see why"
if command -v tectonic >/dev/null && command -v pdftotext >/dev/null; then
  echo "  building a sample resume (the first time downloads LaTeX packages, about a minute)"
  if RESUME_FACTS=resume-builder/facts.example.toml ./resume-builder/build_resume.py examples/sample-spec.json >/dev/null; then
    ok "resume builder works"; rm -rf resumes/tailored/0-sample
  else warn "resume builder failed (see above)"; fi
fi
cat <<'MSG'

Next, in this folder:
  1. Put your resumes in inbox/
  2. Run:  claude
  3. Type: /internship-setup
MSG
