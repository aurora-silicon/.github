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
import io
import json
import re
import sys

MARKER = re.compile(r"hwfact-ok:\s*(.*)")

# Only text that can carry configuration or code. Docs, licenses and lock
# files are skipped.
CHECKED = re.compile(
    r"\.(c|h|rs|py|lua|conf|json|ya?ml|toml|sh|ini|dts|dtsi|dtso)$|(^|/)[^./]+$"
)
SKIPPED = re.compile(r"(^|/)(LICENSE|COPYING|Cargo\.lock)$|\.md$|\.rst$|\.txt$")
# Test fixtures and expected values are meant to be literals.
TESTS = re.compile(r"(^|/)(tests?|fixtures?|testdata)/|(^|/)test_[^/]*$|_tests?\.(rs|py|c)$|kunit")

# Comment syntax per file type. In C and DT sources '#' starts a preprocessor
# directive (or a DT property such as #address-cells), not a comment.
C_LIKE = re.compile(r"\.(c|h|rs|dts|dtsi|dtso|json)$")
LUA = re.compile(r"\.lua$")
COMMENT_C = re.compile(r"^\s*(?://|/\*|\*)")
COMMENT_LUA = re.compile(r"^\s*--")
COMMENT_HASH = re.compile(r"^\s*(?:#|;|//)")


def comment_only(path, text):
    if C_LIKE.search(path):
        return bool(COMMENT_C.match(text))
    if LUA.search(path):
        return bool(COMMENT_LUA.match(text))
    return bool(COMMENT_HASH.match(text))


RATES = r"(?:8_?000|11_?025|16_?000|22_?050|32_?000|44_?100|48_?000|88_?200|96_?000|176_?400|192_?000)"
NUM_SUFFIX = r"(?:\.0+)?(?:_?[uif](?:8|16|32|64|128|size)|[uUlL]+|f)?"

# Identifier words that name a hardware quantity. An identifier is split on
# '_' and camelCase, so 'OPERATING_VOLUME_DB', 'limitDb' and 'max_db' match
# and 'dbus_timeout' does not.
QUANTITY_WORDS = {
    "gain", "volume", "vol", "ceiling", "db", "dbfs", "impedance", "ohm", "ohms", "thermal",
    "temp", "temperature", "celsius", "ramp", "channel", "channels", "amp", "amps", "speaker",
    "speakers", "slot", "slots", "rate", "fs", "hz", "khz", "mv", "voltage",
}
ASSIGN = re.compile(
    r"(?:#\s*define\s+(?P<macro>[A-Za-z_]\w*)\s+\(?\s*[-+]?\d)"
    r"|(?P<name>[A-Za-z_]\w*)[\"']?\s*(?::\s*[&\w<>\[\]; ]+?\s*=|[:=])\s*\[?\s*[-+]?\d"
)
STRING = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
BOARD = re.compile(r"(?i)(?<![a-z0-9])(?:[jk]\d{3}[a-z]{0,2}?|t[68]\d{3})(?:ap)?(?![a-z0-9])")


def words(ident):
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", ident).lower().split("_")
    return {w for w in parts if w}


def quantity(text):
    for m in ASSIGN.finditer(text):
        ident = m.group("macro") or m.group("name")
        if words(ident) & QUANTITY_WORDS:
            return m.group(0).strip()
    return None


def board(text):
    for m in STRING.finditer(text):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        b = BOARD.search(body)
        if b:
            return m.group(0)
    return None


def regex_rule(rx):
    def check(text):
        m = rx.search(text)
        return m.group(0).strip() if m else None
    return check


RULES = [
    (
        "dB level",
        regex_rule(re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?\s*_?(?:dB|dBFS|db)\b")),
        "read the ceiling and scale from the control's TLV (snd_ctl_elem_tlv / amixer cget)",
    ),
    (
        "frequency",
        regex_rule(re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*_?k?Hz\b")),
        "take filter corners and clocks from the matched tuning, DT or codec data, not a literal",
    ),
    (
        "sample rate",
        regex_rule(re.compile(r"(?<![\w.])" + RATES + NUM_SUFFIX + r"(?![\w.])")),
        "read it from the PCM's hw_params constraints or the matched tuning",
    ),
    (
        "hardware quantity",
        quantity,
        "read it from ALSA control info, PCM constraints, the DT or ADT, or the matched tuning",
    ),
    (
        "board or SoC list",
        board,
        "decide from what the machine reports (DT compatible, capability bits), not a board or SoC id",
    ),
]


def added_lines(patch):
    """Yield (new_line_number, text, previous_line_text) for each added line."""
    new = 0
    prev = ""
    for raw in patch.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            new = int(hunk[1])
            prev = ""
            continue
        # '-' lines are not in the new file; '\ No newline at end of file' is not a line.
        if raw.startswith("-") or raw.startswith("\\"):
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
                line = re.sub(r"(^|\s)#.*$", "", line).strip()
                parts = line.split(None, 1)
                if len(parts) == 2 and re.search(r"[A-Za-z]", parts[1]):
                    globs.append(parts[0])
    except FileNotFoundError:
        pass
    return globs


def has_reason(text):
    # A reason has words in it; '-' or an empty marker does not count.
    m = MARKER.search(text)
    return bool(m and len(re.findall(r"[A-Za-z]{2,}", m.group(1))) >= 2)


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
            if comment_only(path, text) or has_reason(text):
                continue
            # The line above only counts when it is a comment that carries the reason.
            if comment_only(path, prev) and has_reason(prev):
                continue
            for name, check, hint in RULES:
                value = check(text)
                if value:
                    findings.append((path, line, name, value, hint))
                    break
    return findings


def report(findings, strict, out=None):
    out = out or sys.stdout
    level = "error" if strict else "warning"
    for path, line, name, value, hint in findings:
        msg = (
            f"'{value}' looks like a hardware fact ({name}) written as a literal: {hint}. "
            "If it is policy or an interface constant, say so with '# hwfact-ok: <reason>' "
            "on the line or in a comment on the line above, or list the path in .hwfact-allow with a reason."
        )
        print(f"::{level} file={path},line={line},title=hwfact-lint::{msg}", file=out)
    print(f"hwfact-lint: {len(findings)} finding(s){' (warnings only)' if findings and not strict else ''}", file=out)
    return 1 if findings and strict else 0


def patch_of(lines):
    return "@@ -0,0 +1,%d @@\n" % len(lines) + "\n".join("+" + l for l in lines)


SELF_TEST = [
    # (path, added line, expected rule or None)
    ("src/a.rs", "let volume_db = 0.0;", "hardware quantity"),
    ("src/a.py", "    rate = 48000", "sample rate"),
    ("src/a.py", "boards = [\"j313\", \"j274\"]", "board or SoC list"),
    ("src/a.py", "# 48000 in a comment is fine", None),
    ("src/a.py", "max_db = -6  # hwfact-ok: policy, safety margin chosen by the owner", None),
    ("src/a.py", "limit = -3 dB  # hwfact-ok:", "dB level"),
    ("src/a.py", "limit = -3 dB  # hwfact-ok: -", "dB level"),
    ("src/a.rs", "let name = \"Speaker Playback Volume\";", None),
    ("conf/a.yaml", "channels: 6", "hardware quantity"),
    ("src/a.py", "    hpf = biquad(100 Hz)", "frequency"),
    ("src/a.py", "dbus_timeout = 5", None),
    # Review of .github#17: each of these passed the first version.
    ("src/codec.c", "#define DEFAULT_RATE 48000", "sample rate"),
    ("src/codec.c", "#define MAX_GAIN_DB -6", "hardware quantity"),
    ("src/codec.c", "#define LIMIT_STR \"-41.8 dB\"", "dB level"),
    ("src/codec.c", "#define BOARD \"j313\"", "board or SoC list"),
    ("src/codec.c", "#include <sound/soc.h>", None),
    ("src/amp.rs", "const OPERATING_VOLUME_DB: f32 = 0.0;", "hardware quantity"),
    ("src/amp.rs", "const MAX_GAIN: f64 = -6.0;", "hardware quantity"),
    ("src/amp.rs", "static AMP_CHANNELS: usize = 6;", "hardware quantity"),
    ("src/amp.rs", "const SAMPLE_RATE: u32 = 48_000;", "sample rate"),
    ("src/amp.rs", "const TARGET_RATE: f32 = 48000.0;", "sample rate"),
    ("src/dsp.rs", "let fs = 48000.0;", "sample rate"),
    ("src/dsp.rs", "set_rate(48_000);", "sample rate"),
    ("src/dsp.rs", "set_rate(48000u32);", "sample rate"),
    ("src/dsp.rs", "set_rate(48000.0_f32);", "sample rate"),
    ("src/dsp.c", "snd_pcm_set_rate(48000U);", "sample rate"),
    ("src/guard.py", "limit_db = -41.8", "hardware quantity"),
    ("src/guard.py", "target_db = 0.0", "hardware quantity"),
    ("src/guard.py", "min_db = -60.0", "hardware quantity"),
    ("src/guard.py", "db = -6.0", "hardware quantity"),
    ("src/board.c", "if (of_machine_is_compatible(\"apple,j313\"))", "board or SoC list"),
    ("src/board.c", "if (of_machine_is_compatible(\"apple,t8132\"))", "board or SoC list"),
    ("src/board.c", "const char *s = \"j873g-speakers\";", "board or SoC list"),
    ("src/board.c", "const char *m = \"J616sAP\";", "board or SoC list"),
    ("src/board.c", "const char *m = \"ti,tas2770\";", None),
]


def self_test():
    failed = []
    for path, text, rule in SELF_TEST:
        got = lint([{"filename": path, "status": "modified", "patch": patch_of([text])}], [])
        name = got[0][2] if got else None
        if name != rule:
            failed.append(f"{path}: {text!r}: got {name}, want {rule}")
    # A reason in a comment on the line above counts; one in a string on the line above does not.
    ok = lint([{"filename": "src/a.py", "status": "modified", "patch": patch_of([
        "# hwfact-ok: interface constant, the ALSA ABI defines this", "SNDRV_PCM_RATE = 44100"])}], [])
    bad = lint([{"filename": "src/a.rs", "status": "modified", "patch": patch_of([
        "let msg = \"hwfact-ok: x y\";", "let rate = 48000;"])}], [])
    if ok or len(bad) != 1:
        failed.append(f"line-above reason: got {len(ok)} and {len(bad)}, want 0 and 1")
    # Line numbers stay right across '\ No newline at end of file'.
    nonl = lint([{"filename": "src/nonl.c", "status": "modified",
                  "patch": "@@ -1 +1,2 @@\n-old\n\\ No newline at end of file\n+int a;\n+#define RATE 48000"}], [])
    if [l for _p, l, *_ in nonl] != [2]:
        failed.append(f"no-newline marker: got lines {[l for _p, l, *_ in nonl]}, want [2]")
    # Skips: test paths, docs, allowlisted paths.
    skipped = lint([
        {"filename": "packaging/session/test_wiring.py", "status": "modified", "patch": patch_of(["rate = 48000"])},
        {"filename": "README.md", "status": "modified", "patch": patch_of(["48000 Hz"])},
        {"filename": "firs/j313/graph.json", "status": "added", "patch": patch_of(["\"gain\": 1.6"])},
    ], ["firs/*/graph.json"])
    if skipped:
        failed.append(f"skips: got {skipped}")
    # Exit status, without printing workflow commands into the self-test's own log.
    sink = io.StringIO()
    if report([("x", 1, "n", "v", "h")], True, sink) != 1 or report([], True, sink) != 0 \
            or report([("x", 1, "n", "v", "h")], False, sink) != 0 or "::error file=x" not in sink.getvalue():
        failed.append("strict exit status or annotation format")
    if failed:
        print("self-test FAILED:\n  " + "\n  ".join(failed), file=sys.stderr)
        return 1
    print(f"hwfact-lint self-test ok ({len(SELF_TEST) + 5} checks)")
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
