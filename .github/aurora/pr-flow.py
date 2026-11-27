#!/usr/bin/env python3
"""Aurora Silicon PR flow: auto-merge arming, reviewer rotation, review SLA,
change-request routing, a live review queue and merge metrics.

One sweep over every open, non-draft pull request in the organisation:

1. State. Each PR is evaluated with the same code as the readiness bot
   (pr-readiness.py next to this file: CI at the head, hardware row at the
   head, reviews, requested changes, conflicts, PR text) plus: base is the
   default branch, holds, exclusions.
2. Merge. When everything but the approval holds, GitHub auto-merge is armed
   (merge commit, expected head pinned), so the PR merges the moment a review
   the ruleset counts lands. When the approval is already in and GitHub shows
   the PR CLEAN, it is merged now (normal merge, head SHA pinned; never the
   admin bypass). Armed PRs are disarmed when the head moves or CI, a change
   request, a conflict or a hold appears. Rulesets are never changed.
3. Reviewers. A PR that needs a review and has no individual review request
   gets one: round-robin over the active reviewers (members of the reviewer
   teams who reviewed recently, never the author), or for PRs not authored by
   our own accounts, over our reviewer lanes (a line in the lane's file).
   After the SLA without a review, the request moves to the next reviewer.
   After a push that follows someone's review, that reviewer is asked again
   with a compare link of the delta since their review.
4. Change requests. Each new change request (or FINISH/HOLD/CLOSE verdict)
   on a PR authored by our accounts is routed to the lane that owns the PR
   (config owners, branch prefixes, or the active lane file that cites it),
   else to the fallback lane.
5. Queue. A markdown queue (repo#PR | needs | who | age) with metrics in its
   header (merged today, median request -> first review -> merge, oldest
   open), regenerated every run, and a batched digest every few hours for
   whoever posts to the team channel.

Configuration: JSON file named by PR_FLOW_CONFIG (see docs/pr-flow.md).
Tokens: GH_TOKEN (a maintainer allowed to merge; writes: auto-merge, merge)
and optionally READ_TOKEN_CMD (a command printing a token for reads, review
requests and comments, e.g. a GitHub App installation token). Tokens are
never printed or stored.

  --dry-run   evaluate and write the queue, change nothing on GitHub or in lane files
  --pr R#N    evaluate one pull request and print its state (implies --dry-run)
"""
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.environ.get("PR_FLOW_CONFIG", os.path.join(HERE, "pr-flow.json"))))
ORG = CFG["org"]
API = "https://api.github.com"
STATE_DIR = CFG["state_dir"]
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NOW = dt.datetime.now(dt.timezone.utc)
H = 3600.0
TZ = dt.timezone(dt.timedelta(hours=CFG.get("utc_offset_h", 0)))

# The readiness evaluator, imported as is so both tools agree on every rule.
_spec = importlib.util.spec_from_file_location("pr_readiness", os.path.join(HERE, "pr-readiness.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

EXTRA = """id headRefName isCrossRepository authorAssociation mergeStateStatus
      autoMergeRequest{enabledAt enabledBy{login}}
      repository{name isPrivate defaultBranchRef{name}}
      reviewRequests(first:20){nodes{requestedReviewer{__typename ... on User{login} ... on Team{slug} ... on Bot{login}}}}
      timelineItems(last:60,itemTypes:[REVIEW_REQUESTED_EVENT,HEAD_REF_FORCE_PUSHED_EVENT,PULL_REQUEST_COMMIT]){nodes{__typename
        ... on ReviewRequestedEvent{createdAt requestedReviewer{__typename ... on User{login} ... on Team{slug}}}
        ... on HeadRefForcePushedEvent{createdAt}
        ... on PullRequestCommit{commit{oid committedDate}}}}
      baseRefName"""
DETAIL_Q = R.DETAIL_Q.replace("headRefOid baseRefName", "headRefOid " + EXTRA, 1)
assert DETAIL_Q != R.DETAIL_Q, "pr-readiness DETAIL_Q changed shape; update EXTRA"

LIST_Q = """
query($q:String!,$after:String){
  search(query:$q,type:ISSUE,first:50,after:$after){
    pageInfo{hasNextPage endCursor}
    nodes{... on PullRequest{number updatedAt headRefOid isDraft mergeable reviewDecision createdAt
      repository{name isPrivate isArchived} author{login __typename}
      labels(first:30){nodes{name}}
      commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}
"""

MERGED_Q = """
query($q:String!,$after:String){
  search(query:$q,type:ISSUE,first:50,after:$after){
    pageInfo{hasNextPage endCursor}
    nodes{... on PullRequest{number url createdAt mergedAt author{login} mergedBy{login}
      repository{name}
      reviews(first:10){nodes{submittedAt author{login}}}
      timelineItems(first:10,itemTypes:[REVIEW_REQUESTED_EVENT]){nodes{... on ReviewRequestedEvent{createdAt}}}}}}}
"""

counts = {}
MEMBERS = set()  # organisation members, public and private (filled per run)


def log(msg):
    print(dt.datetime.now(TZ).strftime("%H:%M:%S"), msg, flush=True)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def hours(t):
    return (NOW - t).total_seconds() / H if t else 0.0


def fmt_age(t):
    h = hours(t)
    return f"{int(h * 60)}m" if h < 1 else f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"


# ---------------------------------------------------------------- GitHub calls

_tokens = {}


def token(kind):
    """'write' = GH_TOKEN (or gh auth token); 'read' = READ_TOKEN_CMD, else write."""
    if kind == "read" and CFG.get("read_token_cmd"):
        t = _tokens.get("read")
        if not t or time.time() - t[1] > 1800:
            out = subprocess.run(CFG["read_token_cmd"], shell=True, capture_output=True, text=True, timeout=60)
            if out.returncode or not out.stdout.strip():
                log("read token command failed; reads use the write token")
                CFG["read_token_cmd"] = ""
                return token("write")
            _tokens["read"] = (out.stdout.strip(), time.time())
        return _tokens["read"][0]
    if "write" not in _tokens:
        t = os.environ.get("GH_TOKEN") or subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
        if not t:
            raise SystemExit("no GH_TOKEN and gh is not logged in")
        _tokens["write"] = (t, time.time())
    return _tokens["write"][0]


class Dry(Exception):
    pass


def call(method, path, body=None, kind="read", tries=4):
    if method != "GET" and path != "/graphql" or (path == "/graphql" and body and body["query"].lstrip().startswith("mutation")):
        if DRY:
            raise Dry()
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token(kind), "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "aurora-pr-flow"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if (e.code in (429, 500, 502, 503, 504) or (e.code == 403 and "rate limit" in msg.lower())) and attempt < tries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1))
                continue
            raise RuntimeError(f"{method} {path}: HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < tries - 1:
                time.sleep(10)
                continue
            raise RuntimeError(f"{method} {path}: {e}")


def gql(query, kind="read", **v):
    r = call("POST", "/graphql", {"query": query, "variables": v}, kind=kind)
    if r.get("errors"):
        raise RuntimeError("GraphQL: " + json.dumps(r["errors"])[:400])
    return r["data"]


def search(query, q):
    out, after = [], None
    while True:
        d = gql(query, q=q, after=after)["search"]
        out += [n for n in d["nodes"] if n]
        if not d["pageInfo"]["hasNextPage"]:
            return out
        after = d["pageInfo"]["endCursor"]


def write(what, key, fn):
    """Run one GitHub write under the per-run cap for its kind."""
    cap = CFG["caps"].get(what, 20)
    if counts.get(what, 0) >= cap:
        return "capped"
    try:
        fn()
    except Dry:
        counts[what] = counts.get(what, 0) + 1
        log(f"DRY {what} {key}")
        return "dry"
    except RuntimeError as e:
        log(f"FAIL {what} {key}: {e}")
        return "fail"
    counts[what] = counts.get(what, 0) + 1
    log(f"{what} {key}")
    return "ok"


# ---------------------------------------------------------------- evaluation

def reviewer_logins(pr):
    out = []
    for n in pr["reviewRequests"]["nodes"]:
        rr = n.get("requestedReviewer") or {}
        if rr.get("__typename") in ("User", "Bot") and rr.get("login"):
            out.append(rr["login"])
    return out


def last_push(pr):
    t = [ts(pr["createdAt"])]
    for n in pr["timelineItems"]["nodes"]:
        if n["__typename"] == "HeadRefForcePushedEvent":
            t.append(ts(n["createdAt"]))
        elif n["__typename"] == "PullRequestCommit":
            t.append(ts(n["commit"]["committedDate"]))
    return max(x for x in t if x)


def excluded(repo, n, labels, author):
    key = f"{repo}#{n}"
    if key in CFG["exclude_prs"] or any(re.fullmatch(p, repo) for p in CFG["exclude_repos"]):
        return "excluded (owner lane / sunset repo)"
    if (author or "").lower() in [x.lower() for x in CFG["deny_logins"]]:
        return "excluded (denied login)"
    if labels & set(CFG["no_merge_labels"]):
        return "held: " + ",".join(sorted(labels & set(CFG["no_merge_labels"])))
    return ""


OWED_RE = re.compile(r"\brows?\b[^.\n]{0,40}\b(still )?(owed|pending|outstanding|missing|to come|to run|not (yet )?(run|posted|done))|\b(still to run|rows? owed|owed rows?)\b", re.I)


def strict_rows(pr, repo, head, labels):
    """Stricter than the readiness row check, for arming and merging (ORCH5 16:16, binding).

    Returns '' when the PR may be armed/merged, else why not:
    - the PR says rows are owed ('rows still owed', 'Still to run', a 'Not yet:' that names rows,
      hardware or a Mac);
    - no line with the head SHA, a Mac and a result (body or comment);
    - repos in regression_repos need head-SHA rows on two different Macs (target + regression).
    'No runtime change.' in Tested and the maintainer label hw-row-ok skip the Mac count, not the owed check.
    """
    sec, _, _, _ = R.structure(pr["body"] or "")
    tested, notyet = sec.get("tested", ""), sec.get("not yet", "")
    no_runtime = re.search(r"no runtime (change|effect)", tested, re.I)
    if OWED_RE.search(tested) or (notyet and (re.search(r"\b(rows?|hardware|regression)\b", notyet, re.I) or R.MAC_RE.search(notyet))
                                  and not re.fullmatch(r"\W*(nothing|none|n/?a)\W*", notyet, re.I)):
        return "rows owed (Tested / Not yet)"
    if OWED_RE.search(R.strip_comments(pr["body"] or "").split("<details", 1)[0]):
        return "rows owed (PR text)"
    if no_runtime:
        return ""
    if R.HWOK_LABEL in labels:
        return ""
    macs = set()
    texts = [R.strip_comments(pr["body"] or "")] + [R.strip_comments(c.get("body") or "") for c in pr["comments"]["nodes"] if R.MARKER not in (c.get("body") or "")]
    for t in texts:
        for line in t.splitlines():
            if R.is_row(line, head):
                names = [m.group(0).lower().replace(" ", "") for m in R.MAC_RE.finditer(line)]
                boards = [x for x in names if re.fullmatch(r"j\d{3}[a-z]{0,2}", x)]
                macs.update(boards or names[:1])  # 'J413 M2 Air' is one Mac
    if not macs:
        return "no hardware row at the head SHA"
    if repo in CFG.get("regression_repos", []) and len(macs) < 2:
        return f"head-SHA row on one Mac only ({', '.join(sorted(macs))}); needs the regression row on a second Mac"
    return ""


def evaluate(pr):
    """Readiness verdict plus what pr-flow needs. Pure: no GitHub writes."""
    head = pr["headRefOid"]
    repo = pr["repository"]["name"]
    want, _, _, _, new_pr = R.evaluate(pr, [], "")
    ci, ci_msg = R.ci_state(pr)
    labels = {l["name"] for l in pr["labels"]["nodes"]}
    author = (pr["author"] or {}).get("login", "ghost")
    base_ok = pr["baseRefName"] == ((pr["repository"].get("defaultBranchRef") or {}).get("name"))
    excl = excluded(repo, pr["number"], labels, author)
    template_ok = "needs-template" not in want or not new_pr
    blockers = []
    if ci == "failing":
        blockers.append("ci")
    elif ci == "pending":
        blockers.append("ci-pending")
    if "needs-hw-row" in want:
        blockers.append("hw-row")
    if "changes-requested" in want:
        blockers.append("fix")
    if "merge-conflict" in want:
        blockers.append("conflict")
    if pr["mergeable"] == "UNKNOWN":
        blockers.append("mergeable?")
    if not template_ok:
        blockers.append("template")
    if not base_ok:
        blockers.append("base")
    if excl:
        blockers.append("excluded")
    rows_why = strict_rows(pr, repo, head, labels) if "hw-row" not in blockers else "no hardware row at the head SHA"
    if rows_why and "hw-row" not in blockers:
        blockers.append("hw-row")
    need_review = "needs-review" in want
    reviews = [r for r in pr["reviews"]["nodes"] if r.get("author") and r["author"]["login"] != author and r.get("submittedAt")]
    last_by = {}
    for r in reviews:
        last_by[r["author"]["login"]] = r
    manual = ""
    if repo in CFG.get("manual_repos", []):
        manual = "policy repo"
    elif labels & set(CFG.get("manual_labels", [])):
        manual = ",".join(sorted(labels & set(CFG["manual_labels"])))
    elif CFG.get("manual_title_re") and re.search(CFG["manual_title_re"], pr["title"], re.I):
        manual = "boot object / Stage 1"
    return {
        "v": EV_VERSION,
        "rows_why": rows_why,
        "manual": manual,
        "repo": repo, "n": pr["number"], "url": pr["url"], "title": " ".join(pr["title"].split())[:70],
        "author": author, "ours": author in CFG["our_accounts"], "member": author in MEMBERS or pr.get("authorAssociation") in ("OWNER", "MEMBER", "COLLABORATOR"),
        "private": pr["repository"]["isPrivate"], "head": head, "base": pr["baseRefName"], "base_ok": base_ok,
        "branch": pr["headRefName"], "created": pr["createdAt"], "last_push": last_push(pr).isoformat(),
        "ci": ci, "ci_msg": ci_msg, "ready": "ready-to-merge" in want, "need_review": need_review,
        "blockers": blockers, "excluded": excl, "labels": sorted(labels), "mss": pr["mergeStateStatus"],
        "decision": pr["reviewDecision"], "id": pr["id"],
        "auto": bool(pr.get("autoMergeRequest")),
        "auto_by": ((pr.get("autoMergeRequest") or {}).get("enabledBy") or {}).get("login"),
        "requested": reviewer_logins(pr),
        "teams": [((n.get("requestedReviewer") or {}).get("slug")) for n in pr["reviewRequests"]["nodes"] if (n.get("requestedReviewer") or {}).get("slug")],
        "req_at": {((n.get("requestedReviewer") or {}).get("login") or ""): n["createdAt"] for n in pr["timelineItems"]["nodes"]
                   if n["__typename"] == "ReviewRequestedEvent" and n.get("requestedReviewer")},
        "reviews": {who: {"state": r["state"], "at": r["submittedAt"], "oid": (r.get("commit") or {}).get("oid", ""), "url": r["url"],
                          "body": (r.get("body") or "")[:1500]} for who, r in last_by.items()},
    }


# ---------------------------------------------------------------- reviewers

def active_reviewers(state):
    """Members of the reviewer teams with a review in the last N days (cached 6 h)."""
    c = state.get("reviewers", {})
    if c.get("at") and hours(ts(c["at"])) < 6:
        return c["active"]
    members = set()
    for t in CFG["reviewer_teams"]:
        try:
            members |= {m["login"] for m in call("GET", f"/orgs/{ORG}/teams/{t}/members?per_page=100", kind="write")}
        except RuntimeError as e:
            log(f"team {t}: {e}")
    since = (NOW - dt.timedelta(days=CFG["active_days"])).strftime("%Y-%m-%d")
    active = {}
    for m in sorted(members - set(CFG["our_accounts"]) - set(CFG["never_request"])):
        q = f"org:{ORG} is:pr reviewed-by:{m} updated:>={since}"
        n = gql("query($q:String!){search(query:$q,type:ISSUE,first:1){issueCount}}", q=q)["search"]["issueCount"]
        if n >= CFG["active_min_reviews"]:
            active[m] = n
    state["reviewers"] = {"at": NOW.isoformat(), "active": active}
    log(f"active reviewers: {active}")
    return active


def load_members(state):
    """All organisation members incl. private ones (needs an org member's token; cached 6 h)."""
    c = state.get("members", {})
    if not (c.get("at") and hours(ts(c["at"])) < 6):
        logins, page = [], 1
        try:
            while True:
                r = call("GET", f"/orgs/{ORG}/members?per_page=100&page={page}", kind="write")
                logins += [m["login"] for m in r]
                if len(r) < 100:
                    break
                page += 1
            c = {"at": NOW.isoformat(), "logins": logins}
            state["members"] = c
        except RuntimeError as e:
            log(f"members: {e}")
    MEMBERS.update(c.get("logins", []))


def pick(pool, load, caps, exclude):
    cands = [p for p in pool if p not in exclude and load.get(p, 0) < caps.get(p, caps.get("*", 10))]
    if not cands:
        return None
    return min(cands, key=lambda p: (load.get(p, 0), pool.index(p)))


# ---------------------------------------------------------------- lanes

def lane_index():
    """PR key -> owning lane, from the active lane files that cite the PR."""
    lanes_dir = CFG["lanes_dir"]
    active = []
    try:
        for line in open(CFG["active_lanes_file"]):
            parts = line.split()
            if len(parts) >= 3 and not line.startswith("#"):
                active.append(parts[2])
    except OSError:
        pass
    skip = set(CFG["not_owner_lanes"])
    score = {}
    rx = re.compile(r"github\.com/" + ORG + r"/([\w.-]+)/pull/(\d+)|\b([a-z][\w.-]+)#(\d{2,5})\b")
    for lane in active:
        if lane in skip:
            continue
        try:
            text = open(os.path.join(lanes_dir, lane + ".md"), errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            opened = bool(re.search(r"\b(opened|open(ed)? PR|PR opened|pushed|my PR|our PR|undrafted)\b", line, re.I))
            for m in rx.finditer(line):
                repo, n = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                k = f"{repo}#{n}"
                s = score.setdefault(k, {})
                s[lane] = s.get(lane, 0) + (5 if opened else 1)
    return {k: max(v.items(), key=lambda x: x[1])[0] for k, v in score.items()}


def owner_lane(ev, index):
    k = f"{ev['repo']}#{ev['n']}"
    if k in CFG["owners"]:
        return CFG["owners"][k]
    for prefix, lane in CFG["branch_lanes"].items():
        if ev["branch"].startswith(prefix):
            return lane
    if index.get(k):
        return index[k]
    lanes = CFG.get("fallback_lanes") or [CFG["fallback_lane"]]
    return lanes[ev["n"] % len(lanes)]


def lane_append(lane, lines):
    if not lines or DRY:
        for l in lines:
            log(f"DRY lane {lane}: {l[:160]}")
        return
    path = os.path.join(CFG["lanes_dir"], lane + ".md")
    with open(path, "a") as f:
        f.write("".join(l.rstrip() + "\n" for l in lines))


def oneline(text, n=280):
    text = re.sub(r"<details>.*?</details>", "", R.strip_comments(text or ""), flags=re.S | re.I)
    return " ".join(text.split())[:n]


# ---------------------------------------------------------------- actions

EV_VERSION = 3  # bump when the arm/merge gate changes: older cached evaluations never arm or merge
HOLDS = {}  # PR key -> lane line that holds it (filled per run by lane_holds)
HOLD_RE = re.compile(r"\bHOLD\b|do ?n[o']t merge|do-not-merge|\bFAIL(ED|S|URES?)?\b|\bregress(ion|ed|es)\b|\breverted?\b|rows? owed", re.I)
CLEAR_RE = re.compile(r"\bverdict\W{0,8}MERGE\b|\bPASS(ED)?\b.*\b(no|0) (regressions?|fail)|\bmerge[- ]ready\b|\bhold (lifted|released|cleared)\b", re.I)


def lane_holds():
    """A lane's latest line about a PR says HOLD / FAIL / regression / revert / rows owed -> do not arm or merge."""
    rx = re.compile(r"github\.com/" + ORG + r"/([\w.-]+)/pull/(\d+)|\b([a-z][\w.-]+)#(\d{2,5})\b")
    latest = {}
    for path in CFG.get("hold_scan_files", []):
        try:
            lines = open(path, errors="replace").read().splitlines()[-CFG.get("hold_scan_lines", 600):]
        except OSError:
            continue
        for line in lines:
            if "PR-FLOW ->" in line:
                continue  # our own assignment lines
            for m in rx.finditer(line):
                repo, n = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                latest[f"{repo}#{n}"] = (os.path.basename(path), line)
    HOLDS.clear()
    for k, (f, line) in latest.items():
        if HOLD_RE.search(line) and not CLEAR_RE.search(line):
            HOLDS[k] = f"{f}: {' '.join(line.split())[:160]}"


def approved_at_head(ev):
    """A non-author approval submitted on the head commit. Repos whose ruleset keeps approvals
    after a push (mesa) would otherwise merge a forward-merged head on an old approval (mesa#700)."""
    return any(r["state"] == "APPROVED" and r.get("oid") == ev["head"] for r in ev["reviews"].values())


def can_arm(ev):
    """Everything but the approval holds, and auto-merge exists for this repo."""
    if f"{ev['repo']}#{ev['n']}" in HOLDS:
        return False
    return (not ev["private"] and not ev["blockers"] and not ev.get("manual")) and ev["ci"] in ("ok", "none")


ARMED_PER_REPO = {}
repo_actions = {}  # repo -> merges/arms this run (serialize: two CLEAN PRs can still break the tip together)


def do_merge_actions(ev, st, merged_log):
    """Arm, disarm or merge one PR. Returns True when it wrote something."""
    before = sum(counts.values())
    _merge_actions(ev, st, merged_log)
    return sum(counts.values()) != before


def _merge_actions(ev, st, merged_log):
    key = f"{ev['repo']}#{ev['n']}"
    armed = st.get("armed")
    # Disarm what we armed when the PR stopped qualifying or the head moved.
    # GitHub computes mergeability lazily: UNKNOWN alone is no reason to disarm.
    keep = dict(ev, blockers=[b for b in ev["blockers"] if b != "mergeable?"])
    if ev["auto"] and not armed and ev.get("auto_by") in CFG["our_accounts"] and not can_arm(keep):
        armed = {"head": ev["head"]}  # armed by our account outside pr-flow's state: the binding gate still applies
    if ev["auto"] and armed and (armed["head"] != ev["head"] or not can_arm(keep)):
        why = ("head moved" if armed["head"] != ev["head"] else
               ",".join(ev["blockers"] + ([ev["rows_why"]] if ev.get("rows_why") else [])) or
               ("lane hold: " + HOLDS[key] if key in HOLDS else ev["ci"]))
        if write("disarm", f"{key} ({why})", lambda: gql(
                "mutation($id:ID!){disablePullRequestAutoMerge(input:{pullRequestId:$id}){clientMutationId}}", kind="write", id=ev["id"])) in ("ok", "dry"):
            st.pop("armed", None)
        return
    if not ev["auto"] and armed:
        st.pop("armed", None)  # someone disarmed it, or GitHub did after a push
    if not can_arm(ev):
        return
    serial = ev["repo"] in CFG.get("serial_repos", [])
    if repo_actions.get(ev["repo"], 0) >= CFG.get("merges_per_repo_per_run", 1):
        return
    if ev["ready"] and ev["mss"] in ("CLEAN", "HAS_HOOKS") and ev["decision"] in ("APPROVED", None) and approved_at_head(ev):
        repo_actions[ev["repo"]] = repo_actions.get(ev["repo"], 0) + 1
        r = write("merge", key, lambda: call("PUT", f"/repos/{ORG}/{ev['repo']}/pulls/{ev['n']}/merge",
                                              {"merge_method": "merge", "sha": ev["head"]}, kind="write"))
        if r == "ok":
            merged_log.append((key, ev["url"], "merged by pr-flow", ev["head"]))
            st["merged_by_flow"] = NOW.isoformat()
        return
    if serial and ARMED_PER_REPO.get(ev["repo"], 0) >= 1:
        return  # serial repos: one armed PR at a time, so approvals can't land two untested-together merges
    if not ev["auto"] and ev["mss"] == "BLOCKED":
        # Only BLOCKED (by the review rule) PRs are armed: a CLEAN PR that is
        # not ready would merge at once without the review it still needs.
        r = write("arm", key, lambda: gql(
            "mutation($id:ID!,$h:GitObjectID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:MERGE,expectedHeadOid:$h}){clientMutationId}}",
            kind="write", id=ev["id"], h=ev["head"]))
        if r in ("ok", "dry"):
            st["armed"] = {"head": ev["head"], "at": NOW.isoformat()}
            ARMED_PER_REPO[ev["repo"]] = ARMED_PER_REPO.get(ev["repo"], 0) + 1


def needs_of(ev, st):
    """(needs, who) for the queue."""
    if ev["excluded"]:
        return "hold", ev["excluded"]
    if f"{ev['repo']}#{ev['n']}" in HOLDS:
        return "hold", "lane: " + HOLDS[f"{ev['repo']}#{ev['n']}"].replace("|", "/")
    b = ev["blockers"]
    if "base" in b:
        return "base", f"stacked on `{ev['base']}`: retarget to the default branch after its base lands"
    if ev["ready"]:
        if ev.get("manual"):
            return "merge (by hand)", f"a maintainer ({ev['manual']})"
        return "merge", "auto-merge armed" if ev["auto"] else "merge now"
    if "fix" in b:
        return "fix", st.get("route", {}).get("lane") or ev["author"]
    if "conflict" in b:
        return "conflict", st.get("route", {}).get("lane") or ev["author"]
    if "ci" in b:
        return "ci", ev["author"] if not ev["ours"] else (st.get("route", {}).get("lane") or "author lane")
    asg = st.get("assigned")
    who = asg["who"] if asg else (", ".join(ev["requested"]) or ("team: " + ", ".join(ev["teams"]) if ev["teams"] else "nobody"))
    if ev["need_review"] and "hw-row" in b:
        return "review+row", who
    if ev["need_review"]:
        return ("review (armed)" if ev["auto"] else "review"), who
    if "hw-row" in b:
        return "hw-row", f"{ev['author']} ({ev.get('rows_why') or 'row'})"
    if "template" in b:
        return "template", ev["author"]
    if "ci-pending" in b or "mergeable?" in b:
        return "ci-pending", "-"
    return "approval", who


def assign_reviews(evs, state, active, index):
    """Round-robin review requests, SLA reassignment, re-review after a push."""
    T = dt.datetime.now(TZ).strftime("%H:%M")
    sla = CFG["sla_hours"]
    humans = sorted(active, key=lambda m: -active[m])
    lanes = CFG["reviewer_lanes"]
    # Current load: open assignments we made.
    load = {}
    for k, st in state["prs"].items():
        a = st.get("assigned")
        if a:
            load[a["who"]] = load.get(a["who"], 0) + 1
    lane_lines = {}

    def order(ev):
        # Ready-except-review first, outside contributors first, oldest first.
        return (0 if ev["need_review"] and not ev["blockers"] else 1, 0 if not ev["member"] else 1, ev["created"])

    for ev in sorted(evs, key=order):
        key = f"{ev['repo']}#{ev['n']}"
        st = state["prs"].setdefault(key, {})
        a = st.get("assigned")
        if ev["excluded"] or "base" in ev["blockers"]:
            continue
        # Re-review: someone reviewed an older head, and the author pushed since.
        for who, r in ev["reviews"].items():
            if who == ev["author"] or not r["oid"] or r["oid"] == ev["head"] or r["state"] not in ("CHANGES_REQUESTED", "APPROVED", "COMMENTED"):
                continue
            if r["state"] == "COMMENTED" and not re.search(r"verdict[^\w\n]{0,8}(FINISH|HOLD)", r["body"], re.I):
                continue
            if r["state"] == "APPROVED" and not ev["need_review"]:
                continue
            done = st.setdefault("rereq", {})
            if done.get(who) == ev["head"] or "fix" in ev["blockers"] and r["state"] != "CHANGES_REQUESTED":
                continue
            if r["state"] == "CHANGES_REQUESTED" and ts(ev["last_push"]) <= ts(r["at"]):
                continue
            cmp = f"https://github.com/{ORG}/{ev['repo']}/compare/{r['oid'][:12]}..{ev['head'][:12]}"
            if who in CFG["our_accounts"] or who.endswith("[bot]") or who in CFG["app_logins"]:
                lane = (a or {}).get("who") if (a or {}).get("kind") == "lane" else CFG["reviewer_lanes"][0]
                lane_lines.setdefault(lane, []).append(f"RE-REVIEW {ev['url']} at {ev['head'][:8]} (delta since your {r['state'].lower()} review: {cmp})")
                done[who] = ev["head"]
            elif who in active or who in CFG.get("rerequest_ok", []):
                def go(who=who, cmp=cmp, ev=ev, oid=r["oid"]):
                    if who not in ev["requested"]:
                        call("POST", f"/repos/{ORG}/{ev['repo']}/pulls/{ev['n']}/requested_reviewers", {"reviewers": [who]})
                    state.setdefault("rereview_asks", []).append({"who": who, "url": ev["url"], "cmp": cmp, "at": NOW.isoformat()})
                    if not CFG.get("rereview_comment"):
                        return  # people get one batched list (digest) instead of a comment per PR
                    call("POST", f"/repos/{ORG}/{ev['repo']}/issues/{ev['n']}/comments",
                         {"body": f"@{who} new commits since your review at `{oid[:8]}`: {cmp} (head `{ev['head'][:8]}`). Re-check the earlier points only.\n\n<sub>pr-flow re-review request</sub>"})
                if write("rerequest", f"{key} {who}", go) in ("ok", "dry"):
                    done[who] = ev["head"]
        if not ev["need_review"] or "fix" in ev["blockers"]:
            if a and not ev["need_review"]:
                st.pop("assigned", None)
            continue
        if a:
            ids = [a["who"]] if a["kind"] == "user" else CFG["our_accounts"] + CFG["app_logins"]
            reviewed = any(ev["reviews"].get(i) and ts(ev["reviews"][i]["at"]) > ts(a["at"]) for i in ids)
            if reviewed:
                st.pop("assigned", None)
                a = None
            elif hours(ts(a["at"])) < sla:
                continue
        others = [x for x in ev["requested"] if x not in CFG["our_accounts"] and x in active and (not a or x != a["who"])]
        if not a and others:
            req_t = min(ts(ev["req_at"].get(x)) or ts(ev["created"]) for x in others)
            if hours(req_t) < sla:
                continue  # an active human is already asked and inside the SLA
        tried = set(st.get("tried", []))
        if ev["ours"] and not CFG.get("lanes_review_ours"):
            pool, kind = humans, "user"
        elif not ev["ours"]:
            pool = [l for l in lanes if ev["repo"] in CFG["lane_repos"].get(l, [ev["repo"]]) or not CFG["lane_repos"].get(l)]
            kind = "lane"
        else:
            pool, kind = lanes, "lane"
        caps = CFG["human_caps"] if kind == "user" else CFG["lane_caps"]
        excl = {ev["author"]} | tried | ({a["who"]} if a else set())
        who = pick(pool, load, caps, excl) or pick(pool, load, caps, {ev["author"]} | ({a["who"]} if a else set()))
        if not who:
            continue
        prev = a["who"] if a else None
        if kind == "user":
            def go(who=who, ev=ev, prev=prev):
                call("POST", f"/repos/{ORG}/{ev['repo']}/pulls/{ev['n']}/requested_reviewers", {"reviewers": [who]})
                if prev and prev not in CFG["reviewer_lanes"]:
                    call("DELETE", f"/repos/{ORG}/{ev['repo']}/pulls/{ev['n']}/requested_reviewers", {"reviewers": [prev]})
            r = write("request", f"{key} -> {who}" + (f" (SLA, was {prev})" if prev else ""), go)
            if r not in ("ok", "dry"):
                continue
        else:
            why = "ready except review" if not ev["blockers"] else "needs " + ",".join(ev["blockers"]) + " too"
            lane_lines.setdefault(who, []).append(
                f"REVIEW {ev['url']} at {ev['head'][:8]} by {ev['author']} ({why}; SLA {sla} h, by {(dt.datetime.now(TZ) + dt.timedelta(hours=sla)).strftime('%H:%M')})"
                + (f" [reassigned from {prev} after {sla} h]" if prev else ""))
        load[who] = load.get(who, 0) + 1
        if prev:
            load[prev] = max(0, load.get(prev, 1) - 1)
        st["assigned"] = {"who": who, "kind": kind, "at": NOW.isoformat(), "head": ev["head"]}
        st["tried"] = sorted(tried | {who})
        st.setdefault("first_assigned", NOW.isoformat())
    for lane, items in lane_lines.items():
        lane_append(lane, [f"- {T} PR-FLOW -> {lane}: {x}" for x in items[:CFG['lane_lines_per_run']]])
        if len(items) > CFG["lane_lines_per_run"]:
            log(f"{lane}: {len(items) - CFG['lane_lines_per_run']} more lane lines held for the next run")


def route_changes(evs, state, index):
    T = dt.datetime.now(TZ).strftime("%H:%M")
    out = {}
    for ev in evs:
        if not ev["ours"] or ev["excluded"]:
            continue
        key = f"{ev['repo']}#{ev['n']}"
        st = state["prs"].setdefault(key, {})
        routed = set(st.get("routed", []))
        for who, r in ev["reviews"].items():
            blocking = r["state"] == "CHANGES_REQUESTED" or re.search(r"verdict[^\w\n]{0,8}(FINISH|HOLD|CLOSE)", r["body"], re.I)
            if not blocking or r["url"] in routed:
                continue
            if r["oid"] and r["oid"] != ev["head"] and ts(ev["last_push"]) > ts(r["at"]):
                continue  # already answered with a push; the re-review path handles it
            lane = owner_lane(ev, index)
            if len(out.get(lane, [])) >= CFG["lane_lines_per_run"]:
                continue  # the rest go next run
            out.setdefault(lane, []).append(
                f"CHANGES REQUESTED {ev['url']} by {who} at {(r['oid'] or ev['head'])[:8]}: {oneline(r['body']) or '(see inline comments)'} "
                f"(review: {r['url']}). Fix every item, push once, reply per item; PR-FLOW re-requests {who} on your push.")
            routed.add(r["url"])
            st["route"] = {"lane": lane, "at": NOW.isoformat()}
        st["routed"] = sorted(routed)[-20:]
    for lane, items in out.items():
        lane_append(lane, [f"- {T} PR-FLOW -> {lane}: {x}" for x in items])
    return out


# ---------------------------------------------------------------- metrics + queue

def median_h(xs):
    xs = [x for x in xs if x is not None and x >= 0]
    return f"{statistics.median(xs):.1f} h" if xs else "n/a"


def metrics(state):
    m = state.get("metrics", {})
    if m.get("at") and hours(ts(m["at"])) < CFG["metrics_every_h"]:
        return m
    midnight = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    out = {"at": NOW.isoformat()}
    for label, since in (("today", midnight), ("7d", midnight - dt.timedelta(days=6))):
        q = f"org:{ORG} is:pr is:merged merged:>={since.isoformat(timespec='seconds')}"
        try:
            prs = search(MERGED_Q, q)
        except RuntimeError as e:
            log(f"metrics {label}: {e}")
            continue
        r2f, f2m, o2m = [], [], []
        for p in prs:
            author = (p["author"] or {}).get("login")
            req = [ts(n["createdAt"]) for n in p["timelineItems"]["nodes"] if n.get("createdAt")]
            rv = [ts(n["submittedAt"]) for n in p["reviews"]["nodes"] if n.get("submittedAt") and (n.get("author") or {}).get("login") != author]
            start = min(req) if req else ts(p["createdAt"])
            merged = ts(p["mergedAt"])
            o2m.append((merged - ts(p["createdAt"])).total_seconds() / H)
            if rv:
                r2f.append((min(rv) - start).total_seconds() / H)
                f2m.append((merged - min(rv)).total_seconds() / H)
        out[label] = {"merged": len(prs), "req_to_review": median_h(r2f), "review_to_merge": median_h(f2m), "open_to_merge": median_h(o2m),
                      "same_day": sum(1 for x in o2m if x <= 24)}
    state["metrics"] = out
    return out


def render_queue(evs, state, m, flow_merges):
    now_l = dt.datetime.now(TZ)
    rows = []
    for ev in evs:
        st = state["prs"].get(f"{ev['repo']}#{ev['n']}", {})
        needs, who = needs_of(ev, st)
        since = st.get("needs_since") if st.get("needs") == needs else None
        if not since:
            st["needs"], st["needs_since"] = needs, (ev["last_push"] if needs in ("review", "review+row", "review (armed)", "hw-row", "ci", "approval") else NOW.isoformat())
            since = st["needs_since"]
        rows.append((needs, ev, who, ts(since)))
    order = ["merge", "merge (by hand)", "review (armed)", "approval", "review", "review+row", "fix", "conflict", "ci", "hw-row", "ci-pending", "template", "base", "hold"]
    rows.sort(key=lambda r: (order.index(r[0]) if r[0] in order else 99, 0 if not r[1]["member"] else 1, r[3] or NOW))
    by = {}
    for r in rows:
        by[r[0]] = by.get(r[0], 0) + 1
    oldest = min(evs, key=lambda e: e["created"]) if evs else None
    t, y = m.get("today", {}), m.get("7d", {})
    overdue = sum(1 for r in rows if r[0].startswith("review") and r[3] and hours(r[3]) > CFG["sla_hours"])
    head = [
        "# Review queue (generated by pr-flow; do not edit)",
        "",
        f"Updated {now_l.strftime('%Y-%m-%d %H:%M %Z')} · {len(evs)} open non-draft PRs · tool: `.github/aurora/pr-flow.py` ([docs](https://github.com/{ORG}/.github/blob/main/docs/pr-flow.md))",
        "",
        f"- **Merged today:** {t.get('merged', '?')} ({t.get('same_day', '?')} opened and merged within 24 h; by pr-flow: {flow_merges['today']}) · last 7 days: {str(y.get('merged', '?')) + ('+ (search cap)' if y.get('merged') == 1000 else '')}",
        f"- **Median today:** request -> first review {t.get('req_to_review', 'n/a')} · first review -> merge {t.get('review_to_merge', 'n/a')} · open -> merge {t.get('open_to_merge', 'n/a')} (7 days: {y.get('req_to_review', 'n/a')} / {y.get('review_to_merge', 'n/a')} / {y.get('open_to_merge', 'n/a')})",
        f"- **Oldest open:** " + (f"[{oldest['repo']}#{oldest['n']}]({oldest['url']}) {fmt_age(ts(oldest['created']))} ({oldest['author']})" if oldest else "none"),
        f"- **Waiting:** " + " · ".join(f"{k} {by[k]}" for k in order if k in by) + f" · reviews past the {CFG['sla_hours']} h SLA: {overdue}",
        f"- **Armed for auto-merge:** {sum(1 for e in evs if e['auto'])} (merge the moment a counted approval lands)",
        "",
        "Target: same-day merges. needs: merge = all checks in; review (armed) = only an approval missing, it merges on approval; fix = change request routed to the lane; base = stacked PR.",
        "Outside contributors first within each group, then oldest first.",
        "",
        "| PR | author | needs | who | age | title |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    body = [f"| [{ev['repo']}#{ev['n']}]({ev['url']}) | {ev['author']}{'' if ev['member'] else ' (outside)'} | {needs} | {who} | {fmt_age(since) if since else '?'} | {ev['title'].replace('|', '/')} |"
            for needs, ev, who, since in rows]
    return "\n".join(head + body) + "\n", rows


def outbox(rows, state, m, flow_merges, merged_since):
    last = state.get("outbox_at")
    if last and hours(ts(last)) < CFG["outbox_every_h"]:
        return
    pub = [r for r in rows if not r[1]["private"]]
    armed = [r for r in pub if r[0] == "review (armed)"]
    by_who = {}
    for r in armed:
        by_who.setdefault(r[2], []).append(r[1]["url"])
    over = [r for r in pub if r[0].startswith("review") and r[3] and hours(r[3]) > CFG["sla_hours"]]
    t = m.get("today", {})
    now_l = dt.datetime.now(TZ).strftime("%m-%d %H:%M")
    lines = [f"## {now_l} PR flow batch (pr-flow, for the team agents channel)", "",
             f"Merged today {t.get('merged', '?')} (by pr-flow {flow_merges['today']}); median request -> first review {t.get('req_to_review', 'n/a')}, first review -> merge {t.get('review_to_merge', 'n/a')}. "
             f"{len(armed)} PRs have CI + hardware row in and merge automatically on one approval; {len(over)} reviews are past the {CFG['sla_hours']} h SLA."]
    if by_who:
        lines += ["", "One approval merges these (CI + row in, auto-merge armed):"]
        for who, urls in sorted(by_who.items(), key=lambda x: -len(x[1])):
            lines.append(f"- {who}: " + " ".join(urls[:CFG["outbox_urls_per_person"]]) + (f" (+{len(urls) - CFG['outbox_urls_per_person']} more in lanes/REVIEW-QUEUE.md)" if len(urls) > CFG["outbox_urls_per_person"] else ""))
    asks = state.get("rereview_asks", [])
    if asks:
        lines += ["", "Re-review asked after a push (delta since your review):"]
        per = {}
        for x in asks:
            per.setdefault(x["who"], []).append(f"{x['url']} ({x['cmp']})")
        for who, items in per.items():
            lines.append(f"- {who}: " + " · ".join(items[:CFG["outbox_urls_per_person"]]))
    if merged_since:
        lines += ["", f"Merged by pr-flow since the last batch: " + " ".join(merged_since[:15])]
    lines += ["", "Full queue: lanes/REVIEW-QUEUE.md", ""]
    if DRY:
        log("DRY outbox:\n" + "\n".join(lines))
        return
    with open(CFG["outbox_file"], "a") as f:
        f.write("\n".join(lines) + "\n")
    state["outbox_at"] = NOW.isoformat()
    state["outbox_merged"] = []
    state["rereview_asks"] = []


def atomic_write(path, text):
    tmp = path + ".pr-flow.tmp"
    open(tmp, "w").write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pr")
    a = ap.parse_args()
    DRY = a.dry_run or bool(a.pr) or os.path.exists(os.path.join(STATE_DIR, "PAUSE"))
    os.makedirs(STATE_DIR, exist_ok=True)
    if a.pr:
        load_members({})
        repo, n = a.pr.split("#")
        pr = gql(DETAIL_Q, owner=ORG, name=repo, n=int(n))["repository"]["pullRequest"]
        ev = evaluate(pr)
        ev.pop("reviews")
        print(json.dumps(ev, indent=1))
        print("can_arm:", can_arm(ev), "needs:", needs_of(ev, {}))
        return
    lock = open(os.path.join(STATE_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run holds the lock; exiting")
        return
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    state.setdefault("prs", {})
    load_members(state)
    start = time.time()

    listing = search(LIST_Q, f"org:{ORG} is:pr is:open draft:false archived:false")
    listing = [p for p in listing if (p.get("author") or {}).get("__typename") != "Bot" and not p["repository"]["isArchived"]]
    open_keys = {f"{p['repository']['name']}#{p['number']}" for p in listing}
    log(f"{len(listing)} open non-draft PRs")

    # PRs that left the open list: merged by us?
    merged_log = []
    for k in [k for k in state["prs"] if k not in open_keys]:
        st = state["prs"].pop(k)
        if st.get("armed") and not st.get("merged_by_flow"):  # direct merges are logged when made
            repo, n = k.split("#")
            try:
                p = call("GET", f"/repos/{ORG}/{repo}/pulls/{n}")
                if p.get("merged_at"):
                    by = (p.get("merged_by") or {}).get("login")
                    how = "auto-merged (armed by pr-flow)"
                    merged_log.append((k, p["html_url"], how, p.get("merge_commit_sha", "")))
            except RuntimeError as e:
                log(f"{k}: {e}")

    evs = []
    detail_budget = CFG["detail_budget_s"]
    for p in sorted(listing, key=lambda p: p["createdAt"]):
        k = f"{p['repository']['name']}#{p['number']}"
        st = state["prs"].setdefault(k, {})
        roll = (((p["commits"]["nodes"] or [{}])[0].get("commit") or {}).get("statusCheckRollup") or {}).get("state")
        fp = "|".join(str(x) for x in (p["headRefOid"], p["updatedAt"], roll, p["mergeable"], p["reviewDecision"], sorted(l["name"] for l in p["labels"]["nodes"])))
        if st.get("fp") != fp or not st.get("ev") or st["ev"].get("v") != EV_VERSION or hours(ts(st.get("ev_at", "2000-01-01T00:00:00+00:00"))) > CFG["refresh_h"]:
            if time.time() - start > detail_budget:
                if st.get("ev"):
                    evs.append(st["ev"])
                continue
            try:
                pr = gql(DETAIL_Q, owner=ORG, name=p["repository"]["name"], n=p["number"])["repository"]["pullRequest"]
                ev = evaluate(pr)
            except (RuntimeError, KeyError, TypeError) as e:
                log(f"{k}: {e}")
                if st.get("ev"):
                    evs.append(st["ev"])
                continue
            st["fp"], st["ev"], st["ev_at"] = fp, ev, NOW.isoformat()
        evs.append(st["ev"])

    lane_holds()
    extra = set()
    for ev in sorted(evs, key=lambda e: state["prs"][f"{e['repo']}#{e['n']}"].get("armed", {}).get("at", "")):
        st = state["prs"][f"{ev['repo']}#{ev['n']}"]
        if ev["auto"] and st.get("armed") and st["armed"]["head"] == ev["head"]:
            if ev["repo"] in CFG.get("serial_repos", []) and ARMED_PER_REPO.get(ev["repo"], 0) >= 1:
                extra.add(f"{ev['repo']}#{ev['n']}")  # serial repo: keep only the oldest armed
                continue
            ARMED_PER_REPO[ev["repo"]] = ARMED_PER_REPO.get(ev["repo"], 0) + 1
    for ev in evs:
        st = state["prs"][f"{ev['repo']}#{ev['n']}"]
        if ev.get("v") != EV_VERSION:
            ev = dict(ev, blockers=list(ev["blockers"]) + ["stale-eval"])
        if f"{ev['repo']}#{ev['n']}" in extra:
            ev = dict(ev, blockers=list(ev["blockers"]) + ["serial"])  # never arm/merge on an old evaluation; disarms ours
        if do_merge_actions(ev, st, merged_log):
            st["fp"] = ""  # re-read next run so the queue shows GitHub's view

    index = lane_index()
    active = active_reviewers(state)
    assign_reviews(evs, state, active, index)
    route_changes(evs, state, index)

    # Merge ledger + metrics.
    led = os.path.join(STATE_DIR, "merges.tsv")
    if merged_log and not DRY:
        with open(led, "a") as f:
            for k, url, how, sha in merged_log:
                f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()}\t{k}\t{url}\t{how}\t{sha}\n")
        state.setdefault("outbox_merged", []).extend(url for _, url, _, _ in merged_log)
    midnight = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    flow_today = 0
    if os.path.exists(led):
        for line in open(led):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and ts(f[0]) >= midnight and ("pr-flow" in f[3]):
                flow_today += 1
    flow = {"today": flow_today}
    m = metrics(state)
    text, rows = render_queue(evs, state, m, flow)
    if DRY:
        atomic_write(os.path.join(STATE_DIR, "REVIEW-QUEUE.dry.md"), text)
    else:
        atomic_write(CFG["queue_file"], text)
    outbox(rows, state, m, flow, state.get("outbox_merged", []))
    if not DRY:
        state["last_run"] = NOW.isoformat()
        atomic_write(STATE_FILE, json.dumps(state))
    log(f"done in {time.time() - start:.0f}s: {len(evs)} PRs; actions {counts}; merged/auto-merged seen {len(merged_log)}")


if __name__ == "__main__":
    main()
