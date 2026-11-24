#!/usr/bin/env bash
# Self-test for pr-text.sh.
# Usage: bash .github/aurora/pr-text-test.sh
set -eu -o pipefail

SCRIPT=$(cd "$(dirname "$0")" && pwd)/pr-text.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
count=0
check() { count=$((count + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

echo "# pull request text (pr-text)"
text() { STRUCTURE_SINCE=2026-11-25T00:00:00Z PR_CREATED=${CREATED:-2026-11-26T00:00:00Z} bash "$SCRIPT" >/dev/null 2>&1; }
GOOD='**What:** admit the fourth T6040 firmware image so J616s gets a render node. Fixes #12

**Tested:** J616s (T6040) at `3f2a9c1`: GPU up on two boots, 0 failed units.

**Risk:** two-way · M4 Pro

**Not yet:** nothing

<details><summary>Evidence</summary>

Tested: this line inside the fold is not the field.
</details>
'
check "complete body passes" 'PR_TITLE="drm/asahi: admit" PR_BODY="$GOOD" text'
check "headings instead of bold labels pass" 'PR_BODY="## Tested
J413 at abc1234: ok
## Risk
two-way, M2" text'
check "missing Tested fails" '! PR_BODY="${GOOD/\*\*Tested:\*\*/\*\*Checked:\*\*}" text'
check "Tested left as the template comment fails" '! PR_BODY="**Tested:** <!-- Macs -->
**Risk:** two-way · M2" text'
check "Tested: no runtime change passes" 'PR_BODY="**Tested:** no runtime change
**Risk:** two-way · none (no runtime change)" text'
check "missing Risk fails" '! PR_BODY="${GOOD/\*\*Risk:\*\* two-way · M4 Pro/}" text'
check "Risk without a door fails" '! PR_BODY="${GOOD/two-way · /}" text'
check "Risk without Macs fails" '! PR_BODY="${GOOD/ · M4 Pro/}" text'
check "Risk 'one-way door, all Macs' passes" 'PR_BODY="${GOOD/two-way · M4 Pro/one-way door, all Macs}" text'
check "Assisted-by line in the body fails" '! PR_BODY="$GOOD
Assisted-by: LLM (Claude:Opus 5.5)" text'
check "Generated-with footer fails" '! PR_BODY="$GOOD
Generated with [Claude Code](https://claude.com/claude-code)" text'
check "attribution line in the title fails" '! PR_TITLE="Assisted-by: Codex" PR_BODY="$GOOD" text'
check "attribution inside a template comment is ignored" 'PR_BODY="$GOOD<!-- Assisted-by: x -->" text'
check "prose naming the rule passes" 'PR_BODY="$GOOD
CI rejects \`Assisted-by:\` lines." text'
printf "commit 1234567890abcdef\nfix\n\nSigned-off-by: A <a@example.org>\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n" > "$WORK/agent"
printf "commit 1234567890abcdef\nfix\n\nCo-authored-by: Jane Doe <jane@example.org>\nSigned-off-by: A <a@example.org>\n" > "$WORK/human"
check "human names that contain tool words pass" 'PR_BODY="$GOOD
Co-authored-by: Devin Smith <d@example.org>
Co-authored-by: Jo Allmendinger <j@example.org>
Signed-off-by: Claude Dupont <c@example.org>" text'
check "a trailer quoted in a code block passes" 'PR_BODY="$GOOD
\`\`\`
Assisted-by: LLM (X)
\`\`\`" text'
check "Copilot co-author fails" '! PR_BODY="$GOOD
Co-authored-by: Copilot <175728472+Copilot@users.noreply.github.com>" text'
check "agent co-author in a commit fails" '! COMMITS_FILE="$WORK/agent" PR_BODY="$GOOD" text'
check "human co-author in a commit passes" 'COMMITS_FILE="$WORK/human" PR_BODY="$GOOD" text'
check "older pull request only warns" 'CREATED=2026-11-20T00:00:00Z PR_BODY="hello" text'
check "draft only warns" 'PR_DRAFT=true PR_BODY="hello" text'
check "ready new pull request without the lines fails" '! PR_BODY="hello" text'

# A check that passes everything is worse than no check: the workflow
# relies on these to fail loudly.
[ "$count" -ge 22 ] || { echo "FAIL only $count checks ran"; fail=1; }
exit $fail
