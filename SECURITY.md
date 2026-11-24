# Security policy

## Reporting a vulnerability

**Do not open a public issue, PR or Discord thread for a security problem.**

Report it privately through GitHub: open the affected repository, go to the **Security** tab and choose **Report a vulnerability** (private vulnerability reporting is enabled on our public repositories). If you are not sure which repository is affected, report it against [`aurora-silicon/linux`](https://github.com/aurora-silicon/linux/security/advisories/new).

Include what is affected (repository, release tag or commit, Mac), how to reproduce it, and the impact. Do not include serial numbers or other personal data.

## What happens next

- An Owner acknowledges the report, usually within three days.
- The fix is developed privately (a draft security advisory or private fork), not in a public PR.
- If a stable release is affected, it gets a hotfix release ([hotfix policy](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/hotfix.md)); the fix is forward-ported to `main`.
- The advisory is published once a fixed release is available, crediting the reporter unless they ask otherwise.

## Supported versions

The latest stable `v*` release and the `main` branch (shipped as `aurora-edge` and `aurora-nightly`). Older stable releases are not patched; upgrade to the latest.

Problems in upstream projects we build on (the Linux kernel, Mesa, U-Boot) that are not specific to Aurora should also be reported to those projects through their own security processes.
