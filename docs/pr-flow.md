# PR flow

[`.github/aurora/pr-flow.py`](../.github/aurora/pr-flow.py) keeps pull requests moving between the
readiness checks and the merge. It runs every 10 minutes on a maintainer's machine (a systemd timer)
over every open, non-draft pull request in the organisation. It uses the [readiness bot](pr-readiness.md)'s
own evaluation, so both always agree on CI, hardware row, reviews, change requests and conflicts.

## What a run does

| Step | When | What it does |
| --- | --- | --- |
| Merge | CI at the head passed (or the change has no CI), a hardware row is at the head, no change request, no conflict, no `hold`/`blocked`/`do-not-merge`, base is the default branch | The approval is the only thing missing: turns on GitHub **auto-merge** (merge commit, head SHA pinned), so the PR merges the moment a review the ruleset counts lands. Approval already in and GitHub shows it mergeable: merges it now (normal merge, head pinned). |
| Row gate | Before arming or merging | Stricter than the readiness label: a line with the head SHA, a Mac and a result; for linux, mesa, aura, installer and u-boot a second Mac (the regression row); nothing in Tested or Not yet saying rows are owed or still to run; no lane line holding the PR (HOLD, FAIL, regression, revert). One merge per repository per run, and one armed PR at a time on mesa (no CI gate there). |
| Disarm | The head moved, or CI, a change request, a conflict or a hold appeared | Turns off the auto-merge it turned on. A PR is never armed while GitHub would merge it without a review. |
| Reviewer | Needs a review and no active reviewer is asked | Asks one, round-robin by load: members of Core, Omarchy Mac and Aurora Maintainers who reviewed in the last 7 days (never the author); for PRs not authored by the lab's own account, the lab's reviewer agents. Ready PRs first, outside contributors first, then oldest first. |
| SLA | The request is 4 hours old without a review | Moves it to the next reviewer. |
| Re-review | The author pushed after someone's review | Asks that reviewer again, with a compare link of the delta since their review. |
| Change request | A reviewer requests changes (or posts a FINISH/HOLD/CLOSE verdict) on a lab PR | Routes the finding to the lab agent that owns the PR. |
| Queue | Every run | Writes the review queue (PR, needs, who, age) with merged-today and median times in its header, and a digest every 2 hours. |

It never changes rulesets, never uses the admin bypass and never approves anything: rulesets decide
when a PR may merge, people and review agents decide whether it should.

## For authors and reviewers

- Want it merged on approval? Get CI green and put the hardware row at the head in the PR (or say
  `No runtime change.`). The queue shows `review (armed)` when only an approval is missing.
- Pushed after a review? Nothing to do: the reviewer is asked again with the delta.
- Keep a PR out: label it `hold`, `blocked` or `do-not-merge`. Auto-merge is turned off on the next run.
- Auto-merge needs a public repository on the organisation's plan; private repositories are listed,
  not armed.

## Running it

```
PR_FLOW_CONFIG=config.json python3 .github/aurora/pr-flow.py --dry-run   # evaluate, change nothing
PR_FLOW_CONFIG=config.json python3 .github/aurora/pr-flow.py --pr linux#123
```

The config names the organisation, the state directory, the reviewer teams and agent pools, caps per
run, the SLA and the excluded pull requests. A `PAUSE` file in the state directory makes every run a dry run.
Writes use `GH_TOKEN` (or `gh auth token`); reads, review requests and comments can use a separate token
command (`read_token_cmd`, e.g. a GitHub App installation token). Tokens are never printed or stored.
