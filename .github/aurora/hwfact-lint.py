#!/usr/bin/env python3
"""Flag hardware facts written as literals in the lines a pull request adds.

Rule (agent-guidelines pre-pr-checklist.md, "No hardware fact as a literal"):
hardware facts come from the machine (ALSA control info and TLV, PCM
constraints, ADT, DT, matched tuning, firmware replies), not from numbers or
board lists in our code. This lint looks at added lines only and reports the
ones that look like such a fact: dB levels, sample rates, gains, ceilings,
channel or amp counts, impedances, thermal limits, ramp times, and board or
SoC lists.

It is a heuristic and starts as a warning. A line is accepted when it, or the
line above it, carries a reason:

    max_db = -6.0  # hwfact-ok: policy, safety margin Ryan set 2026-11-27

or when a `.hwfact-allow` file in the repository root covers the path:

    # path glob            reason
    firs/*/graph.json      voicing assets generated from the matched tuning

An empty reason does not count.

Input is the PR's file list as returned by the GitHub API
(`gh api repos/O/R/pulls/N/files --paginate`, slurped into one JSON array),
so the PR's code is never checked out or run.

    hwfact-lint.py --files files.json [--allow .hwfact-allow] [--strict]
    hwfact-lint.py --self-test

Exit status: 0 when clean or in warning mode, 1 on findings with --strict,
2 on bad input.
"""

import argparse
import fnmatch
import json
import re
import sys

MARKER = re.compile(r"hwfact-ok:\s*(\S.*)")

# Only text that can carry configuration or code. Docs, licenses and lock
# files are skipped.
CHECKED = re.compile(
    r"\.(c|h|rs|py|lua|conf|json|ya?ml|toml|sh|ini|dts|dtsi|dtso)$|(^|/)[^./]+$"
)
SKIPPED = re.compile(r"(^|/)(LICENSE|COPYING|Cargo\.lock)$|\.md$|\.rst$|\.txt$")
# Test fixtures and expected values are meant to be literals.
TESTS = re.compile(r"(^|/)(tests?|fixtures?|testdata)/|(^|/)test_[^/]*$|_tests?\.(rs|py|c)$|kunit")

RULES = [
    (
        "dB level",
        re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?\s*_?(?:dB|dBFS|db)\b"),
        "read the ceiling and scale from the control's TLV (snd_ctl_elem_tlv / amixer cget)",
    ),
    (
        "frequency",
        re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*_?k?Hz\b"),
        "take filter corners and clocks from the matched tuning, DT or codec data, not a literal",
    ),
    (
        "sample rate",
        re.compile(r"(?<![\w.])(?:8000|11025|16000|22050|32000|44100|48000|88200|96000|176400|192000)(?![\w.])"),
        "read it from the PCM's hw_params constraints or the matched tuning",
    ),
    (
        "hardware quantity",
        re.compile(
            r"(?i)\b\w*(?:gain|volume|ceiling|max_?db|impedance|ohms?|thermal|temp(?:erature)?|celsius"
            r"|ramp|channels?|amps?|speakers?|slots?|rate)\w*\"?\s*[:=]\s*\[?\s*[-+]?\d"
        ),
        "read it from ALSA control info, PCM constraints, the DT or ADT, or the matched tuning",
    ),
    (
        "board or SoC list",
        re.compile(r"(?i)[\"'](?:[jk]\d{3}[a-z]{0,2}|t[68]\d{3})[\"']"),
        "decide from what the machine reports (DT compatible, capability bits), not a board list",
    ),
]

COMMENT_ONLY = re.compile(r"^\s*(?://|/?\*|#|--|;)")


def added_lines(patch):
    """Yield (new_line_number, text, previous_added_or_context_text)."""
    new = 0
    prev = ""
    for raw in patch.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            new = int(hunk[1])
            prev = ""
            continue
        if raw.startswith("-"):
            continue
        text = raw[1:]
        if raw.startswith("+"):
            yield new, text, prev
        prev = text
        new += 1


def load_allow(path):
    globs = []
    if not path:
        return globs
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip() if line.lstrip().startswith("#") else line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].strip():
                    globs.append(parts[0])
    except FileNotFoundError:
        pass
    return globs


def has_reason(text):
    m = MARKER.search(text)
    return bool(m and m.group(1).strip())


def lint(files, allow):
    findings = []
    for f in files:
        path = f.get("filename", "")
        patch = f.get("patch")
        if not patch or f.get("status") == "removed":
            continue
        if SKIPPED.search(path) or TESTS.search(path) or not CHECKED.search(path):
            continue
        if any(fnmatch.fnmatch(path, g) for g in allow):
            continue
        for line, text, prev in added_lines(patch):
            if COMMENT_ONLY.match(text) or has_reason(text) or has_reason(prev):
                continue
            for name, rx, hint in RULES:
                m = rx.search(text)
                if m:
                    findings.append((path, line, name, m.group(0).strip(), hint))
                    break
    return findings


def report(findings, strict):
    level = "error" if strict else "warning"
    for path, line, name, value, hint in findings:
        msg = (
            f"'{value}' looks like a hardware fact ({name}) written as a literal: {hint}. "
            "If it is policy or an interface constant, say so with '# hwfact-ok: <reason>' "
            "on the line or the line above, or list the path in .hwfact-allow with a reason."
        )
        print(f"::{level} file={path},line={line},title=hwfact-lint::{msg}")
    print(f"hwfact-lint: {len(findings)} finding(s){' (warnings only)' if findings and not strict else ''}")
    return 1 if findings and strict else 0


def self_test():
    patch = "\n".join([
        "@@ -1,2 +1,12 @@",
        " context",
        "+let volume_db = 0.0;",
        "+    rate = 48000",
        "+boards = [\"j313\", \"j274\"]",
        "+# 48000 in a comment is fine",
        "+max_db = -6  # hwfact-ok: policy, safety margin chosen by the owner",
        "+# hwfact-ok: interface constant, the ALSA ABI defines this",
        "+    SNDRV_PCM_RATE = 44100",
        "+limit = -3 dB  # hwfact-ok:",
        "+let name = \"Speaker Playback Volume\";",
        "+channels: 6",
        "+    hpf = biquad(100 Hz)",
    ])
    files = [
        {"filename": "src/a.rs", "status": "modified", "patch": patch},
        {"filename": "packaging/session/test_wiring.py", "status": "modified", "patch": "@@ -0,0 +1 @@\n+rate = 48000"},
        {"filename": "README.md", "status": "modified", "patch": "@@ -0,0 +1 @@\n+48000 Hz"},
        {"filename": "firs/j313/graph.json", "status": "added", "patch": "@@ -0,0 +1 @@\n+\"gain\": 1.6"},
    ]
    got = [(p, l, n) for p, l, n, _v, _h in lint(files, ["firs/*/graph.json"])]
    want = [
        ("src/a.rs", 2, "hardware quantity"),
        ("src/a.rs", 3, "sample rate"),
        ("src/a.rs", 4, "board or SoC list"),
        ("src/a.rs", 9, "dB level"),
        ("src/a.rs", 11, "hardware quantity"),
        ("src/a.rs", 12, "frequency"),
    ]
    if got != want:
        print(f"self-test FAILED\n got:  {got}\n want: {want}", file=sys.stderr)
        return 1
    if report([("x", 1, "n", "v", "h")], True) != 1 or report([], True) != 0:
        print("self-test FAILED: strict exit status", file=sys.stderr)
        return 1
    print("hwfact-lint self-test ok")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--files", help="JSON array of PR files from the GitHub API")
    ap.add_argument("--allow", help="path to the repository's .hwfact-allow")
    ap.add_argument("--strict", action="store_true", help="fail on findings")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.files:
        ap.error("--files is required")
    try:
        with open(a.files, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"::error title=hwfact-lint::cannot read {a.files}: {e}")
        return 2
    # `gh api --paginate --slurp` gives a list of pages; flatten either shape.
    files = [x for page in data for x in (page if isinstance(page, list) else [page])]
    return report(lint(files, load_allow(a.allow)), a.strict)


if __name__ == "__main__":
    sys.exit(main())
