# Runbooks

Operational procedures for the SlopStopper project, covering both the suite itself and the things adopters do once around it.

## Overview

This directory holds step-by-step runbooks for common operational tasks: adopting the suite, installing the Claude Code skills, moving an adopter's CLI pin and cutting a release. The Snyk account procedures (service account, token rotation, regional tenants) live with the security docs in [SNYK.md](../security/SNYK.md), routed from the map.

## Routes

| When you are…                                                              | Do this                                                                                                   |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| adopting slopstopper in a repo, or asking what the suite installs and needs | Read [INSTALL.md](./INSTALL.md) first for prerequisites, the tagged-checkout install, what lands, what each check needs, the Snyk settings, configuration and updates |
| installing or refreshing the Claude Code skill duo in a repo               | Read [INSTALL_SKILLS.md](./INSTALL_SKILLS.md) for `install-skill.sh`, what lands under `<repo>/.claude/skills/` and how to uninstall |
| moving an adopter repo to a newer `slopstopper-cli` tag, or when CI and local disagree on the CLI version | Read [UPGRADE_CLI.md](./UPGRADE_CLI.md) for the `cli.ref` pin, re-running `install.sh` from the new checkout, and recovery |
| cutting a `slopstopper-cli` release                                        | Read [RELEASE.md](./RELEASE.md) for the version bump, the changelog entry, the annotated tag and what adopters do next |

## Adding Runbooks

When operational procedures are needed, add them here as individual markdown files and route them from the table above. Examples:

- `incident-response.md`, steps for responding to a scanner outage (Snyk tenant down, every Snyk check exiting 2)
- `secret-rotation.md`, if the rotation procedure in `SNYK.md` grows beyond the Snyk token
