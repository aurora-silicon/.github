#!/usr/bin/env python3
"""Aurora Silicon merge-readiness bot.

Keeps one sticky comment and a set of labels on every open pull request of a
repository, from the rules in aurora-silicon/agent-guidelines:
policy/merge-policy.md (CI green, hardware row at the head, one non-author
review), policy/pr-structure.md (body sections, Merge risk) and
docs/MAINTAINER-HANDBOOK.md (what the labels mean).

It reads pull requests with GraphQL and writes only labels and its own
comment. It never checks out or runs pull request code.

Environment:
  GITHUB_TOKEN   token with pull-requests/issues write (required)
  REPO           owner/name (required)
  PR             evaluate this pull request only (event mode)
  STATE_FILE     JSON fingerprints from the previous sweep (optional)
  CUTOFF         ISO date; PRs created before it are not marked needs-template
                 and get a comment only once something happens on them
  COMMENT_NEW    1 = a sweep may create the comment on PRs created after CUTOFF
  DRY_RUN        1 = print what would change, change nothing
  MAX_WRITES     cap on write requests per run (default 400)
  TIME_BUDGET    seconds to spend on pull requests per run (default 720);
                 the rest wait for the next sweep
"""
import base64
import calendar
import fnmatch
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

VERSION = "1"
MARKER = "<!-- aurora-pr-readiness"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
REPO = os.environ.get("REPO", "")
DRY = os.environ.get("DRY_RUN") == "1"
CUTOFF = os.environ.get("CUTOFF", "2026-11-25T00:00:00Z")
COMMENT_NEW = os.environ.get("COMMENT_NEW", "1") == "1"
MAX_WRITES = int(os.environ.get("MAX_WRITES", "400"))
TIME_BUDGET = int(os.environ.get("TIME_BUDGET", "720"))
POLICY = "https://github.com/aurora-silicon/agent-guidelines/blob/main"

# Labels this bot owns. Anything else on a PR (hold, soc:*, human labels)
# is never touched, except hw-row-ok, which it removes on a new head.
STATE_LABELS = {
    "ready-to-merge": ("0e8a16", "Meets the merge policy: merge it (bot)"),
    "needs-review": ("fbca04", "No approving review from Core or Omarchy Mac yet (bot)"),
    "needs-hw-row": ("d4c5f9", "No hardware row at the PR head (bot)"),
    "changes-requested": ("e99695", "A reviewer requested changes (bot)"),
    "ci-failing": ("b60205", "A check failed at the PR head (bot)"),
    "merge-conflict": ("b60205", "Conflicts with the base branch (bot)"),
    "needs-template": ("c5def5", "PR body lacks required sections (bot)"),
}
PREFIX_LABELS = {"area:": "1d76db", "blast:": "5319e7", "door:": "bfd4f2"}
HOLD_LABELS = {"hold", "blocked"}  # set by people; the bot never touches them
BOT_LOGINS = {x for x in os.environ.get("BOT_LOGINS", "github-actions,github-actions[bot]").split(",") if x}
TRUSTED = {"OWNER", "MEMBER"}
HWOK_LABEL = "hw-row-ok"
IGNORE_CHECKS = re.compile(r"pr-readiness|readiness", re.I)

GENS = {  # chip -> generation label
    "m1": "T8103 T6000 T6001 T6002",
    "m2": "T8112 T6020 T6021 T6022",
    "m3": "T8122 T6030 T6031 T6032 T6034",
    "m4": "T8132 T6040 T6041",
    "m5": "T8142 T6050 T8152",
    "neo": "T8140",
}
CHIP_GEN = {c: g for g, cs in GENS.items() for c in cs.split()}
MAC_RE = re.compile(r"(?<![\w/.-])(J\d{3}[a-z]{0,2}|T[68]\d{3}|M[1-5](?: (?:Pro|Max|Ultra|Air|mini))?|Neo|MacBook(?: \w+)?|Mac ?mini|iMac|Mac Studio)(?![\w/.-])", re.I)
RESULT_RE = re.compile(r"\b(boots?|booted|row|ran|runs?|tested|tests?|pass(?:es|ed)?|fail(?:s|ed|ures?)?|ok|clean|soak|deqp|cts|kunit|regression|avcs?|units|works?|working|results?|no change)\b", re.I)
NOT_ROW_RE = re.compile(r"^\W*(forward-)?(merged?|rebased?|cherry-picked)\b", re.I)
HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
VERDICT_RE = re.compile(r"^[^\w\n]*verdict[^\w\n]{0,8}(MERGE|FINISH|HOLD|CLOSE)\b", re.I | re.M)

writes = 0


class WriteCap(Exception):
    pass


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def request(method, path, body=None, tries=4):
    global writes
    if method != "GET" and path != "/graphql":
        if DRY:
            log(f"DRY {method} {path}" + ("\n" + body["body"] if body and "body" in body else " " + json.dumps(body)[:200] if body else ""))
            return {}
        if writes >= MAX_WRITES:
            raise WriteCap()
        writes += 1
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aurora-pr-readiness",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            limited = e.code in (429, 502, 503) or (e.code == 403 and ("rate limit" in msg.lower() or e.headers.get("X-RateLimit-Remaining") == "0"))
            if limited and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
                log(f"{method} {path}: HTTP {e.code}, retry in {wait}s: {msg}")
                time.sleep(wait)
                continue
            if e.code == 404 and method == "DELETE":
                return {}
            raise SystemExit(f"{method} {path}: HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < tries - 1:
                time.sleep(10)
                continue
            raise SystemExit(f"{method} {path}: {e}")
    return {}


def graphql(query, **variables):
    r = request("POST", "/graphql", {"query": query, "variables": variables})
    if r.get("errors"):
        raise SystemExit("GraphQL: " + json.dumps(r["errors"])[:500])
    return r["data"]


LIST_Q = """
query($owner:String!,$name:String!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequests(states:OPEN,first:100,after:$after){
      pageInfo{hasNextPage endCursor}
      nodes{number updatedAt headRefOid isDraft mergeable reviewDecision
        labels(first:30){nodes{name}}
        commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}
"""

FIRST_COMMENTS_Q = """
query($owner:String!,$name:String!,$n:Int!){
  repository(owner:$owner,name:$name){pullRequest(number:$n){
    comments(first:100){nodes{databaseId body url author{login} authorAssociation createdAt}}}}}
"""

DETAIL_Q = """
query($owner:String!,$name:String!,$n:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$n){
      number state title body url createdAt updatedAt isDraft mergeable reviewDecision
      headRefOid baseRefName author{login __typename}
      labels(first:50){nodes{name}}
      files(first:100){totalCount nodes{path}}
      latestOpinionatedReviews(first:30){nodes{state author{login} authorAssociation submittedAt url commit{oid}}}
      reviews(last:30){nodes{state body url author{login} authorAssociation submittedAt commit{oid}}}
      comments(last:100){totalCount nodes{databaseId body url author{login} authorAssociation createdAt}}
      commits(last:1){nodes{commit{committedDate statusCheckRollup{state contexts(first:100){nodes{
        __typename
        ... on CheckRun{name status conclusion detailsUrl}
        ... on StatusContext{context state targetUrl}}}}}}}}}}
"""


# ---------------------------------------------------------------- body parsing

def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text or "", flags=re.S)


FIELDS = ("what", "tested", "risk", "not yet")
FIELD_RE = re.compile(r"^(what|tested|risk|not yet)\s*:(.*)$")
HEAD_RE = re.compile(r"^(what|tested|risk|not yet)\s*$")


def fields(body):
    """Visible 'Label:' fields (agent-guidelines policy/pr-structure.md).

    Same rules as agent-policy.sh pr_field: a field starts at a 'Label:'
    line (bold, list marker or heading allowed) or a '## Label' heading and
    runs to the next field, heading or <details>; folded blocks are skipped.
    """
    out, cur, fold = {}, None, 0
    for raw in strip_comments(body).splitlines():
        low_raw = raw.lower()
        if "<details" in low_raw:
            fold += 1
            cur = None
        if "</details>" in low_raw:
            fold = max(0, fold - 1)
            continue
        if fold:
            continue
        l = raw.lstrip()
        head = l.startswith("#")
        l = re.sub(r"^#+\s*", "", l)
        l = re.sub(r"^[-*+]\s+", "", l)
        l = re.sub(r"[*_]", "", l)
        m = FIELD_RE.match(l.lower())
        if m or (head and HEAD_RE.match(l.lower().strip())):
            cur = (m or HEAD_RE.match(l.lower().strip())).group(1)
            out.setdefault(cur, "")
            if m:
                out[cur] += l.split(":", 1)[1].strip() + "\n"
            continue
        if head:
            cur = None
            continue
        if cur:
            out[cur] += raw + "\n"
    return {k: v.strip() for k, v in out.items()}


def structure(body):
    f = fields(body)
    missing = []
    if not f.get("tested"):
        missing.append("a 'Tested:' line")
    risk = " ".join(f.get("risk", "").split())
    door = ""
    m = re.search(r"\b(one|two)[ -]way\b", risk, re.I)
    if m:
        door = m.group(1).lower() + "-way"
    blast = re.sub(r"\b(one|two)[ -]way( door)?\b", "", risk, flags=re.I).strip(" ·,;:-|")
    if not risk:
        missing.append("a 'Risk:' line (door and Macs)")
        # Older PRs (Omarchy Mac's 'Merge Danger' block): labels only.
        text = strip_comments(body)
        m = re.search(r"door\W*:\W*(one|two)[ -]way", text, re.I)
        door = m.group(1).lower() + "-way" if m else ""
        m = re.search(r"blast[ -]?radius\W*:[*_ ]*(.+)", text, re.I)
        blast = m.group(1).strip(" *_") if m else ""
    else:
        if not door:
            missing.append("'one-way' or 'two-way' in Risk")
        if not re.search(r"[A-Za-z0-9]", blast):
            missing.append("the Macs in Risk")
            blast = ""
    return f, missing, door, blast


def blast_labels(blast):
    if not blast:
        return set()
    b = blast.lower()
    if re.search(r"\bnone\b|no runtime", b):
        return {"blast:none"}
    if re.search(r"\ball\b|every mac|fleet", b):
        return {"blast:all"}
    out = {f"blast:m{g}" for g in re.findall(r"\bm([1-5])\b", b)}
    if re.search(r"\bneo\b", b):
        out.add("blast:neo")
    for chip in re.findall(r"\bt[68]\d{3}\b", b):
        g = CHIP_GEN.get(chip.upper())
        if g:
            out.add(f"blast:{g}")
    return out


# ---------------------------------------------------------------- CODEOWNERS areas

def load_areas(owner, name):
    """Parse '# area: <name>' sections of CODEOWNERS -> [(pattern, area)]."""
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        try:
            r = request("GET", f"/repos/{owner}/{name}/contents/{path}")
        except SystemExit:
            continue
        if not r.get("content"):
            continue
        text = base64.b64decode(r["content"]).decode(errors="replace")
        rules, area = [], None
        for line in text.splitlines():
            m = re.match(r"^\s*#\s*area:\s*([a-z0-9-]+)", line, re.I)
            if m:
                area = m.group(1).lower()
                continue
            if re.match(r"^\s*#\s*end[- ]area", line, re.I):
                area = None
                continue
            line = line.split("#", 1)[0].strip()
            if line and area:
                rules.append((line.split()[0], area))
        return rules, r.get("sha", "")
    return [], ""


def co_match(pattern, path):
    """CODEOWNERS (gitignore-style) match, good enough for area labels."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    p = pattern.strip("/")
    is_dir = pattern.endswith("/")
    rx = re.escape(p).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace(r"\?", "[^/]").replace("\0", ".*")
    if not anchored:
        rx = r"(?:.*/)?" + rx
    last = p.rsplit("/", 1)[-1]
    if is_dir or "*" not in last:
        rx += r"(?:/.*)?"  # a directory: everything below it
    return re.fullmatch(rx, path) is not None


def areas_for(files, rules):
    out = set()
    for f in files:
        hit = None
        for pat, area in rules:  # last match wins, as in CODEOWNERS
            if co_match(pat, f):
                hit = area
        if hit:
            out.add("area:" + hit)
    return out


# ---------------------------------------------------------------- evaluation

def short(sha):
    return sha[:8]


def ci_state(pr):
    nodes = (((pr["commits"]["nodes"] or [{}])[0].get("commit") or {}).get("statusCheckRollup") or {})
    ctx = (nodes.get("contexts") or {}).get("nodes") or []
    failing, pending, passed = [], [], 0
    for c in ctx:
        if c["__typename"] == "CheckRun":
            name = c["name"]
            if IGNORE_CHECKS.search(name):
                continue
            if c["status"] != "COMPLETED":
                pending.append(name)
            elif c["conclusion"] in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
                failing.append(f"[{name}]({c.get('detailsUrl') or pr['url']})")
            else:
                passed += 1
        else:
            name = c["context"]
            if IGNORE_CHECKS.search(name):
                continue
            if c["state"] in ("PENDING", "EXPECTED"):
                pending.append(name)
            elif c["state"] in ("FAILURE", "ERROR"):
                failing.append(f"[{name}]({c.get('targetUrl') or pr['url']})")
            else:
                passed += 1
    if failing:
        return "failing", "failing: " + ", ".join(failing[:6])
    if pending:
        return "pending", "running: " + ", ".join(pending[:6])
    if passed:
        return "ok", f"{passed} check{'s' if passed != 1 else ''} passed"
    when = ((pr["commits"]["nodes"] or [{}])[0].get("commit") or {}).get("committedDate") or ""
    if when and time.time() - calendar.timegm(time.strptime(when, "%Y-%m-%dT%H:%M:%SZ")) < 900:
        return "pending", "no checks reported yet for this head"
    return "none", "no CI for this change (the hardware row is the gate)"


def head_prefix_in(text, head):
    for tok in HEX_RE.findall(text.lower()):
        if head.startswith(tok):
            return True
    return False


def is_row(line, head):
    """A hardware row: head SHA, a Mac and a result on one line."""
    return (head_prefix_in(line, head) and MAC_RE.search(line) and RESULT_RE.search(line)
            and not NOT_ROW_RE.search(line))


def find_row(pr, sec, head):
    if re.search(r"no runtime (change|effect)", sec.get("tested", ""), re.I):
        return "ok", "not needed: no runtime change"
    for line in strip_comments(pr["body"] or "").splitlines():
        if is_row(line, head):
            return "ok", f"in the PR body at `{short(head)}`"
    for c in reversed(pr["comments"]["nodes"]):
        body = strip_comments(c["body"] or "")
        if MARKER in (c["body"] or ""):
            continue
        for line in body.splitlines():
            if is_row(line, head):
                return "ok", f"[comment]({c['url']}) at `{short(head)}`"
    return "missing", f"no line with the head SHA `{short(head)}`, a Mac and a result in the PR body or a comment"


def review_state(pr, head):
    author = (pr["author"] or {}).get("login", "")
    approvals, changes = [], []
    for r in pr["latestOpinionatedReviews"]["nodes"]:
        who = (r["author"] or {}).get("login", "ghost")
        if who == author:
            continue
        if r["state"] == "APPROVED":
            approvals.append((who, r))
        elif r["state"] == "CHANGES_REQUESTED":
            changes.append((who, r))
    decision = pr.get("reviewDecision")
    trusted = [w for w, r in approvals if r["authorAssociation"] in ("OWNER", "MEMBER")]
    # Agents that share the author's account post their verdict as a review
    # or comment (policy/pr-review.md). Only members' verdicts count, only a
    # line with one verdict (not the template's list), latest one wins.
    cands = [(r.get("submittedAt") or "", r.get("authorAssociation"), r["body"] or "", r["url"], (r.get("commit") or {}).get("oid", ""))
             for r in pr["reviews"]["nodes"]]
    cands += [(c.get("createdAt") or "", c.get("authorAssociation"), c["body"] or "", c["url"], "")
              for c in pr["comments"]["nodes"] if MARKER not in (c["body"] or "")]
    agent = None
    for when, assoc, body, url, oid in sorted(cands, key=lambda c: c[0]):
        if assoc not in TRUSTED:
            continue
        text = strip_comments(body)
        for m in VERDICT_RE.finditer(text):
            end = text.find("\n", m.start())
            line = text[m.start():end if end >= 0 else len(text)]
            if "|" in line:
                continue
            if oid == head or head_prefix_in(line, head):
                agent = (m.group(1).upper(), url)
    if decision == "APPROVED" or (decision is None and trusted):
        names = ", ".join(sorted({w for w, _ in approvals})) or "approved"
        state = ("ok", f"approved by {names}")
    elif agent and agent[0] == "MERGE":
        state = ("ok", f"[review verdict MERGE at `{short(head)}`]({agent[1]}); no GitHub approval, so an Owner or Core member merges with `--admin`")
    elif approvals:
        state = ("missing", "approved by " + ", ".join(w for w, _ in approvals) + ", but GitHub does not count it yet (code owner from Core or Omarchy Mac needed, or approval went stale)")
    else:
        state = ("missing", "needs one approving review from Core or Omarchy Mac (not the author)")
    ch = ("ok", "none") if not changes else ("open", "requested by " + ", ".join(f"[{w}]({r['url']})" for w, r in changes))
    if agent and agent[0] in ("FINISH", "HOLD", "CLOSE") and not changes:
        ch = ("open", f"[review verdict {agent[0]} at `{short(head)}`]({agent[1]})")
    return state, ch


def bot_comments(pr):
    return [c for c in pr["comments"]["nodes"]
            if (c["body"] or "").startswith(MARKER) and ((c.get("author") or {}).get("login") in BOT_LOGINS)]


def evaluate(pr, rules, prev_hwok):
    head = pr["headRefOid"]
    mine = bot_comments(pr)
    if mine:  # the comment marker is current; the sweep state may be older
        m = re.search(r"hwok=([0-9a-f]*)", mine[-1]["body"])
        prev_hwok = m.group(1) if m else ""
    labels = {l["name"] for l in pr["labels"]["nodes"]}
    sec, missing, door, blast = structure(pr["body"] or "")
    new_pr = pr["createdAt"] >= CUTOFF
    ci, ci_msg = ci_state(pr)
    (rv, rv_msg), (ch, ch_msg) = review_state(pr, head)
    row, row_msg = find_row(pr, sec, head)
    hwok_head = prev_hwok
    if HWOK_LABEL in labels:
        if prev_hwok and prev_hwok != head:
            labels.discard(HWOK_LABEL)
            hwok_head = ""
        else:
            hwok_head = head
            if row != "ok":
                row, row_msg = "ok", f"`{HWOK_LABEL}` set by a maintainer for `{short(head)}`"
    else:
        hwok_head = ""
    conflict = pr["mergeable"] == "CONFLICTING"
    unknown = pr["mergeable"] == "UNKNOWN"
    held = bool(labels & HOLD_LABELS)

    want = set()
    if ci == "failing":
        want.add("ci-failing")
    if conflict:
        want.add("merge-conflict")
    if rv != "ok":
        want.add("needs-review")
    if row != "ok":
        want.add("needs-hw-row")
    if ch == "open":
        want.add("changes-requested")
    if missing:
        want.add("needs-template")
    ready = (ci in ("ok", "none") and rv == "ok" and row == "ok" and ch == "ok"
             and not conflict and not unknown and not held and (not missing or not new_pr))
    if ready:
        want.add("ready-to-merge")
    if door:
        want.add("door:" + door)
    want |= blast_labels(blast)
    files = [f["path"] for f in pr["files"]["nodes"]]
    want |= areas_for(files, rules)

    def mark(ok):
        return "ok" if ok else "**no**"
    lines = [
        f"{MARKER} v={VERSION} head={head} hwok={hwok_head} -->",
        f"**Merge readiness at `{short(head)}`: " + ("ready to merge**" if ready else "not ready yet**"),
        "",
        "| Check | | Detail |",
        "| --- | --- | --- |",
        f"| CI at head | {mark(ci in ('ok', 'none')) if ci != 'pending' else 'running'} | {ci_msg} |",
        f"| Review | {mark(rv == 'ok')} | {rv_msg} |",
        f"| Requested changes | {mark(ch == 'ok')} | {ch_msg} |",
        f"| Hardware row at head | {mark(row == 'ok')} | {row_msg} |",
        f"| Conflicts | {mark(not conflict)} | {'merge conflicts with `' + pr['baseRefName'] + '`: merge it in (no force push)' if conflict else 'none' if pr['mergeable'] == 'MERGEABLE' else 'not computed yet'} |",
    ]
    if missing:
        what = "; ".join(missing)
        lines.append(f"| PR text | {'**no**' if new_pr else 'adopt when you next edit'} | add {what} ([guide]({POLICY}/policy/pr-structure.md)) |")
    else:
        lines.append(f"| PR text | ok | Risk: {door} · {blast} |")
    if held:
        lines.append("| Hold | **no** | labelled `" + "`, `".join(sorted(labels & HOLD_LABELS)) + "` by a maintainer |")
    extra = sorted(l for l in want if l.startswith("area:"))
    lines += ["", "Areas: " + (", ".join(f"`{a}`" for a in extra) if extra else "none matched in CODEOWNERS")]
    if ready:
        lines.append(f"Merge: `gh pr merge {pr['number']} --merge` (Owners and Core through the bypass: add `--admin`).")
    lines += ["", f"<sub>Updated by [pr-readiness](https://github.com/aurora-silicon/.github/blob/main/.github/workflows/pr-readiness.yml) on every push, edit and review, and every 30 minutes. Rules: [merge policy]({POLICY}/policy/merge-policy.md) · [labels](https://github.com/aurora-silicon/.github/blob/main/docs/pr-readiness.md).</sub>"]
    return want, labels, "\n".join(lines), hwok_head, new_pr


# ---------------------------------------------------------------- apply

ensured = set()


def ensure_label(owner, name, label):
    if label in ensured:
        return
    color, desc = STATE_LABELS.get(label, (None, None))
    if color is None:
        for p, c in PREFIX_LABELS.items():
            if label.startswith(p):
                color, desc = c, {"area:": "Area from CODEOWNERS (bot)", "blast:": "Macs affected, from Merge risk (bot)", "door:": "Revertible or not, from Merge risk (bot)"}[p]
    try:
        request("GET", f"/repos/{owner}/{name}/labels/{urllib.request.quote(label, safe='')}")
    except SystemExit:
        request("POST", f"/repos/{owner}/{name}/labels", {"name": label, "color": color or "ededed", "description": desc or ""})
    ensured.add(label)


def owned(label):
    return label in STATE_LABELS or any(label.startswith(p) for p in PREFIX_LABELS)


def apply(owner, name, pr, want, have_after, body, event_pr, new_pr):
    n = pr["number"]
    have = {l["name"] for l in pr["labels"]["nodes"]}
    add = sorted(want - have)
    remove = sorted(l for l in have if (owned(l) and l not in want) or (l == HWOK_LABEL and l not in have_after))
    for l in add:
        ensure_label(owner, name, l)
    if add:
        request("POST", f"/repos/{owner}/{name}/issues/{n}/labels", {"labels": add})
    for l in remove:
        request("DELETE", f"/repos/{owner}/{name}/issues/{n}/labels/{urllib.request.quote(l, safe='')}")
    mine = bot_comments(pr)
    changed = bool(add or remove)
    if mine:
        if mine[-1]["body"].strip() != body.strip():
            request("PATCH", f"/repos/{owner}/{name}/issues/comments/{mine[-1]['databaseId']}", {"body": body})
            changed = True
    elif event_pr or (new_pr and COMMENT_NEW):
        request("POST", f"/repos/{owner}/{name}/issues/{n}/comments", {"body": body})
        changed = True
    log(f"#{n}: {'ready' if 'ready-to-merge' in want else 'not ready'}; +{add} -{remove}{' comment' if changed else ''}")


def main():
    if not TOKEN or "/" not in REPO:
        raise SystemExit("GITHUB_TOKEN and REPO=owner/name are required")
    owner, name = REPO.split("/", 1)
    rules, co_sha = load_areas(owner, name)
    state_file = os.environ.get("STATE_FILE", "")
    state = {}
    if state_file and os.path.exists(state_file):
        try:
            state = json.load(open(state_file))
        except ValueError:
            state = {}
    gen = f"{VERSION}:{co_sha}"
    if state.get("_gen") != gen:
        state = {"_gen": gen}
    only = os.environ.get("PR", "").strip()
    todo = []
    if only:
        todo = [int(only)]
    else:
        after = None
        open_now = set()
        while True:
            d = graphql(LIST_Q, owner=owner, name=name, after=after)["repository"]["pullRequests"]
            for p in d["nodes"]:
                open_now.add(str(p["number"]))
                roll = (((p["commits"]["nodes"] or [{}])[0].get("commit") or {}).get("statusCheckRollup") or {}).get("state")
                fp = "|".join(str(x) for x in (p["headRefOid"], p["updatedAt"], roll, p["mergeable"], p["reviewDecision"], p["isDraft"], sorted(l["name"] for l in p["labels"]["nodes"])))
                if state.get(str(p["number"]), {}).get("fp") != fp:
                    todo.append(p["number"])
                state.setdefault(str(p["number"]), {})["seen"] = fp
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = d["pageInfo"]["endCursor"]
        for k in [k for k in state if k != "_gen" and k not in open_now]:
            del state[k]
        log(f"{REPO}: {len(open_now)} open, {len(todo)} changed since the last sweep")
    start = time.time()
    failed = 0
    try:
        for i, n in enumerate(todo):
            if not only and time.time() - start > TIME_BUDGET:
                log(f"time budget used; {len(todo) - i} PRs left for the next sweep")
                break
            try:
                pr = graphql(DETAIL_Q, owner=owner, name=name, n=n)["repository"]["pullRequest"]
            except SystemExit as e:
                log(f"::warning::#{n}: {e}")
                failed += 1
                continue
            if pr is None or pr["state"] != "OPEN":
                continue
            if pr["comments"]["totalCount"] > len(pr["comments"]["nodes"]) and not bot_comments(pr):
                first = graphql(FIRST_COMMENTS_Q, owner=owner, name=name, n=n)["repository"]["pullRequest"]["comments"]["nodes"]
                pr["comments"]["nodes"] = [c for c in first if (c["body"] or "").startswith(MARKER)] + pr["comments"]["nodes"]
            key = str(n)
            st = state.setdefault(key, {})
            if pr["isDraft"] or (pr["author"] or {}).get("__typename") == "Bot":
                have = {l["name"] for l in pr["labels"]["nodes"]}
                drop = sorted(l for l in have if l in STATE_LABELS)
                try:
                    for l in drop:
                        request("DELETE", f"/repos/{owner}/{name}/issues/{n}/labels/{urllib.request.quote(l, safe='')}")
                except WriteCap:
                    log(f"write cap {MAX_WRITES} reached; the next run continues")
                    break
                log(f"#{n}: draft or bot, skipped" + (f"; -{drop}" if drop else ""))
            else:
                try:
                    want, have_after, body, hwok, new_pr = evaluate(pr, rules, st.get("hwok", ""))
                    apply(owner, name, pr, want, have_after, body, bool(only), new_pr)
                    st["hwok"] = hwok
                except WriteCap:
                    log(f"write cap {MAX_WRITES} reached; the next run continues")
                    break
                except SystemExit as e:
                    log(f"::warning::#{n}: {e}")
                    failed += 1
                    continue
            if "seen" in st:
                # Labels we just wrote change the fingerprint; record the
                # listing fingerprint with our labels so the next sweep
                # does not re-read an unchanged PR.
                st["fp"] = st["seen"] if not only else ""
    finally:
        if state_file:
            for v in state.values():
                if isinstance(v, dict):
                    v.pop("seen", None)
            json.dump(state, open(state_file, "w"))
    log(f"done; {writes} writes, {failed} PRs failed")
    if only and failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
