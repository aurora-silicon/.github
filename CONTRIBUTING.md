# Contributing to Aurora Silicon

Thanks for helping bring Linux to Apple Silicon Macs. This page applies to every `aurora-silicon` repository without a CONTRIBUTING file of its own; a repo's own file adds build and test details and links back here.

**One rulebook for humans and agents: [`agent-guidelines/AGENTS.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/AGENTS.md).** Everything below is the short version.

## In short

1. **Branch and PR.** Branch from the default branch (`main`, or the branch the repo's README names until it moves to `main`): `feat/<topic>` or `fix/<topic>` (or your fork), `wip/<topic>` to park work. Open a PR into the default branch. Kernel version upgrades go on `rebase/v<version>` as a PR; stable fixes go on `hotfix/v<X.Y.Z>`, which Owners create. Never force-push a shared branch. [Branching and releases](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/branching-and-releases.md)
2. **Fill in the PR template.** What, Tested (Macs at the head SHA and the result), Risk (one-way or two-way door, which Macs), Not yet; detail goes in the folded blocks. [PR structure](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/pr-structure.md)
3. **Test on the Mac it affects, at the PR head, with numbers.** No Mac? Say so in the PR and ask for a row; we run it on a lab Mac.
4. **Commits:** human author and a human `Signed-off-by` ([DCO](https://developercertificate.org/)). **No AI attribution anywhere:** no `Assisted-by`, tool `Co-Authored-By`, "Generated with" or model names in commits, PR text, comments or reviews. The check says which line to delete.
5. **No copyrighted Apple material in public:** no Apple code, decompiled or disassembled listings, copied symbol dumps, firmware or tuning files. Reverse-engineering contributions are welcome: drivers, interface layouts and register descriptions written as your own code with named fields. ADT values are hardware facts and fine to cite. [IP policy](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/ip-and-tainted-data.md)
6. **No serial numbers** (Mac, ECID, monitor EDID) in anything public.

## Who merges

A PR merges when CI is green, it was tested on the touched Macs at its head, and someone other than the author approved it with no blocking finding; then it merges the same hour. [Merge policy](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/merge-policy.md)

- **Core**, **Omarchy Mac** and **Aurora Maintainers** members review, approve and merge ([review and merging handbook](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/review-and-merge-handbook.md)).
- **Aurora Dev** contributors develop on branches with CI and lab access, open PRs and review; a Core, Omarchy Mac or Aurora Maintainers member approves and merges.
- **Owners** cut releases and change settings and access. Roles: [GOVERNANCE.md](GOVERNANCE.md).

## Reporting bugs

Search existing issues first. One issue per Mac for test reports. Give the Mac (chip and board), the release or commit you ran, the firmware version, exact steps and how often it happens. [Issue structure](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/issue-structure.md)

Security problems: report privately, see [SECURITY.md](SECURITY.md).

## Changing the rules

Propose a change by PR to [`agent-guidelines`](https://github.com/aurora-silicon/agent-guidelines). Never keep local rules that disagree with it.
