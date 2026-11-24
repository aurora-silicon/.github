# Contributing to Aurora Silicon

Thanks for helping bring Linux to Apple Silicon Macs. This page applies to every `aurora-silicon` repository that has no CONTRIBUTING file of its own; a repo's own file adds build and test details and links back here.

**The rules live in one place: [`aurora-silicon/agent-guidelines`](https://github.com/aurora-silicon/agent-guidelines).** They apply to humans and agents alike, from any provider. Read these before your first PR:

- [`AGENTS.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/AGENTS.md): the hard rules (clean-room only, hardware safety, human-only commit identity, no force pushes, no model gating or hacks).
- [Pre-PR checklist](https://github.com/aurora-silicon/agent-guidelines/blob/main/pre-pr-checklist.md): what reviewers check.
- [Merge policy](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/merge-policy.md): CI green, a hardware row at the PR head, one non-author review, then merge.
- [Branching and releases](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/branching-and-releases.md): target `main` from `feat/` or `fix/` branches; edge, nightly and stable channels.

## In short

1. Branch from `main` (`feat/<topic>` or `fix/<topic>`, or in your fork) and open a pull request into `main` (or the repo's default branch where a repo has not moved to `main` yet).
2. Every commit has a human `Signed-off-by` ([DCO](https://developercertificate.org/)). No agent trailers in commits. If an agent helped, end the PR body with one `Assisted-by:` line naming the model or tool.
3. Fill in the PR template: Problem, Change, Testing, Still to run. Put long detail in collapsible `<details>` blocks.
4. Prove it on the Mac it affects, at the PR head, with numbers. No Mac? Say so in the PR and we will run it on a lab Mac.
5. Never post anything derived from Apple binaries, firmware or tuning values, and never post serial numbers.

## Reporting bugs

Search existing issues first. One issue per Mac for test reports. Give the Mac (chip and board), the release tag or commit you ran, the firmware version, exact steps and how often it happens. See [issue structure](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/issue-structure.md).

Security problems: report privately, see [SECURITY.md](SECURITY.md).

## Roles

Who may review, merge, tag and change settings: [GOVERNANCE.md](GOVERNANCE.md).
