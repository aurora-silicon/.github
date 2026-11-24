#!/usr/bin/env bash
# Canonical PR text check for every Aurora Silicon repository, run by
# .github/workflows/pr-text.yml. It checks the pull request's text, not
# its files: the body has the two lines of agent-guidelines
# policy/pr-structure.md that CI enforces ('Tested:' and 'Risk:' with the
# door and the Macs), and the title, body and the author's own commit
# messages carry no AI attribution lines. Pull requests created before
# STRUCTURE_SINCE, and drafts, get the findings as warnings only.
#
# Usage: pr-text.sh
#
# Environment:
#   PR_TITLE, PR_BODY   the pull request's title and body
#   PR_CREATED          creation time (ISO 8601)
#   PR_DRAFT=true       draft, warn only
#   STRUCTURE_SINCE     enforce on PRs created at or after this time
#                       (default 2026-11-25T00:00:00Z)
#   COMMITS_FILE        the author's commit messages, each starting with a
#                       line "commit <sha>"
#
# Self-test: bash .github/aurora/pr-text-test.sh
set -u -o pipefail

log() { printf '%s\n' "$*" >&2; }

STRUCTURE_DOC=https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/pr-structure.md
# Lines that attribute work to a model or tool (case-insensitive ERE).
# Trailers naming people are fine: only product names and AI-vendor or
# bot addresses count, so "Co-authored-by: Claude Dupont" passes.
AI_NAME='(claude (opus|sonnet|haiku|fable|code)|chatgpt|gpt-[0-9]|github copilot|copilot|openai codex|codex|gemini( [0-9]| code| cli)|cursor agent|devin-ai|@anthropic\.com|@openai\.com|noreply@anthropic|\[bot\]@)'
ATTRIB_RE="^[[:space:]>*_-]*(assisted-by[[:space:]]*:|(co-authored-by|co-developed-by|signed-off-by|reviewed-by|tested-by)[[:space:]]*:.*$AI_NAME|generated (with|by) .*(claude|codex|chatgpt|gpt|gemini|copilot|llm|cursor|an? ai\\b))|claude\\.ai/(code|chat|share)|chatgpt\\.com/(share|c)/|claude-session|🤖"

# strip_html_comments: stdin -> stdout without <!-- ... --> (multi-line).
strip_html_comments() {
	perl -0777 -pe 's/<!--.*?-->//gs'
}

# strip_code: stdin -> stdout without fenced code blocks and inline code,
# so quoting a trailer as an example is not an attribution line.
strip_code() {
	perl -0777 -pe 's/^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$//gms; s/`[^`\n]*`//g'
}

# pr_field <what|tested|risk|not yet>: stdin = body; print the field's text.
# A field starts at a "Label:" line (bold, list marker or heading allowed)
# or a "## Label" heading, and runs to the next field, heading or <details>.
# Folded <details> blocks are not part of the visible text and are skipped.
pr_field() {
	awk -v want="$1" '
	tolower($0) ~ /<details/ { fold++; on = 0 }
	tolower($0) ~ /<\/details>/ { if (fold > 0) fold--; next }
	fold > 0 { next }
	{
		l = $0; sub(/^[ \t]*/, "", l); head = (l ~ /^#/)
		sub(/^#+[ \t]*/, "", l); sub(/^[-*+][ \t]+/, "", l); gsub(/[*_]/, "", l)
		low = tolower(l); name = ""
		if (low ~ /^(what|tested|risk|not yet)[ \t]*:/) {
			name = low; sub(/[ \t]*:.*/, "", name); rest = l; sub(/^[^:]*:[ \t]*/, "", rest)
		} else if (head && low ~ /^(what|tested|risk|not yet)[ \t]*$/) {
			name = low; sub(/[ \t]*$/, "", name); rest = ""
		}
		if (name != "") { on = (name == want); if (on) print rest; next }
		if (head || low ~ /^<details/) { on = 0; next }
		if (on) print $0
	}'
}

pr_text() {
	local body title enforce=1 errs=() tested risk macs line f
	title=${PR_TITLE:-}
	body=$(printf '%s' "${PR_BODY:-}" | tr -d '\r' | strip_html_comments)
	if [ "${PR_DRAFT:-false}" = true ]; then enforce=0; fi
	if [ -n "${PR_CREATED:-}" ] && [[ "$PR_CREATED" < "${STRUCTURE_SINCE:-2026-11-25T00:00:00Z}" ]]; then
		enforce=0
	fi

	tested=$(printf '%s\n' "$body" | pr_field tested | tr -d '[:space:]')
	[ -n "$tested" ] || errs+=("Add a 'Tested:' line: the Macs you ran it on at the head SHA and the result, e.g. 'Tested: J413 M2 Air at abc1234: 0 failed units'. Docs or CI only: 'Tested: no runtime change'.")
	risk=$(printf '%s\n' "$body" | pr_field risk | tr '\n' ' ')
	if [ -z "$(printf '%s' "$risk" | tr -d '[:space:]')" ]; then
		errs+=("Add a 'Risk:' line: the door and the Macs it can affect, e.g. 'Risk: two-way · M4 Pro' (two-way = a plain revert undoes it).")
	else
		printf '%s' "$risk" | grep -q -i -E '(one|two)[ -]way' ||
			errs+=("Risk: say 'one-way' (a revert does not undo it) or 'two-way' (a plain revert undoes it), e.g. 'Risk: two-way · M4 Pro'.")
		macs=$(printf '%s' "$risk" | sed -E 's/(one|two)[ -]way( door)?//I; s/[^[:alnum:]]+//g')
		[ -n "$macs" ] ||
			errs+=("Risk: add which Macs it can affect after the door, e.g. 'two-way · M1, M2' or 'two-way · all Macs' or 'two-way · none (no runtime change)'.")
	fi

	# Attribution: title, body and the author's own commits. Carried
	# third-party commits are not in COMMITS_FILE.
	while IFS= read -r line; do
		errs+=("AI attribution in the title: '$line'. Remove it.")
	done < <(printf '%s\n' "$title" | grep -i -E "$ATTRIB_RE" || true)
	while IFS= read -r line; do
		errs+=("AI attribution in the PR body: '$line'. Delete this line.")
	done < <(printf '%s\n' "$body" | strip_code | grep -i -E "$ATTRIB_RE" || true)
	if [ -n "${COMMITS_FILE:-}" ] && [ -s "$COMMITS_FILE" ]; then
		while IFS= read -r line; do
			errs+=("AI attribution in commit ${line%%: *}: '${line#*: }'. Commits carry the human author and Signed-off-by only: amend it if the branch is yours alone, else recreate the commit without the line on a new branch and open a new PR (never force-push a shared branch).")
		done < <(awk '/^commit [0-9a-f]+$/ { c = substr($2, 1, 12); next } { print c ": " $0 }' "$COMMITS_FILE" |
			grep -i -E "^[0-9a-f]*: ${ATTRIB_RE#^}" || true)
	fi

	if [ ${#errs[@]} -eq 0 ]; then
		log "PR text follows $STRUCTURE_DOC"
		return 0
	fi
	if [ "$enforce" = 1 ]; then
		for f in "${errs[@]}"; do printf '::error title=PR text::%s\n' "$f"; done
		log "Edit the PR description; the check re-runs on every edit. Guide: $STRUCTURE_DOC"
		return 1
	fi
	for f in "${errs[@]}"; do printf '::warning title=PR text::%s\n' "$f"; done
	log "Warnings only (draft, or opened before ${STRUCTURE_SINCE:-2026-11-25T00:00:00Z}). Guide: $STRUCTURE_DOC"
	return 0
}

pr_text
