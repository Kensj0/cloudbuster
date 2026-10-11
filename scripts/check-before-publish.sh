#!/usr/bin/env bash
# Run before every push, and before making the repository public.
#
# Usage: ./scripts/check-before-publish.sh [personal-strings-file]
#
#   1. gitleaks scans the WHOLE git history for secrets
#   2. the history and working tree are searched for your personal strings
#      (names, domains, Tailscale names, e-mail addresses, IPs, paths), listed
#      one per line in a file that lives OUTSIDE the repository
#      (default: ~/.config/cloudbuster/personal-strings.txt)
#   3. checks that no private file is tracked
# Runs as your normal user; needs no sudo. Prints matches, never the strings file.
set -euo pipefail
cd "$(dirname "$0")/.."
STRINGS="${1:-$HOME/.config/cloudbuster/personal-strings.txt}"
fail=0

echo "==> 1. gitleaks (full history)"
if command -v gitleaks >/dev/null; then
  gitleaks git --redact --no-banner . || fail=1
else
  echo "gitleaks is not installed: https://github.com/gitleaks/gitleaks#installing"; fail=1
fi

echo "==> 2. personal strings"
if [[ -s "$STRINGS" ]]; then
  while IFS= read -r s; do
    [[ -z "$s" || "$s" == \#* ]] && continue
    if git log --all -p -i -F -S "$s" --format='%h' | grep -q .; then
      echo "found in git history: a string from line $(grep -nxF -- "$s" "$STRINGS" | cut -d: -f1) of $STRINGS"; fail=1
    fi
    if git grep -i -F -q -- "$s"; then
      echo "found in tracked files: line $(grep -nxF -- "$s" "$STRINGS" | cut -d: -f1) of $STRINGS"
      git grep -i -F -l -- "$s"; fail=1
    fi
  done < "$STRINGS"
else
  echo "no strings file at $STRINGS (create it, one personal string per line)"; fail=1
fi

echo "==> 3. private files"
if git ls-files | grep -E '(^|/)(\.env|config\.yaml|.*\.db|.*\.sqlite3?|.*\.log|docker-compose\.override\.yml)$'; then
  echo "the files above are private and must not be tracked"; fail=1
fi

if [[ $fail -eq 0 ]]; then echo "OK: nothing found"; else echo "STOP: fix the findings above before pushing"; fi
exit $fail
