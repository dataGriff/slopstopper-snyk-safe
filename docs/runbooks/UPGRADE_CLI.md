# Upgrade the pinned `slopstopper-cli`

`slopstopper-cli` is pinned per-repo as a git tag of this repository, under
`cli:` in `.slopstopper.yml`:

```yaml
cli:
  source: https://github.com/dataGriff/slopstopper-snyk-safe.git
  ref: v0.1.0
```

CI reads it in `.github/actions/ss-setup` and installs
`slopstopper-cli @ git+<source>@<ref>#subdirectory=cli` on every run, so the
version everyone runs is the one in the committed file and a breaking
release cannot reach your repo until you choose to move the pin. A review of
the bump PR is the review of the new CLI.

## How the pin behaves

- **First install** writes the tag `install.sh` ran from into the starter
  config. Commit it so CI installs the same version.
- **A plain `install.sh` re-run** from the same checkout never changes the
  pin; refreshes only pull new workflows, shims and composite actions.
- **CI** installs exactly `ref`. `SLOPSTOPPER_CLI_SPEC`, when set as a
  repository or organisation variable, replaces the pin with a pip
  requirement served by an internal index (`slopstopper-cli==0.1.0`) and
  wins over the yml.
- Locally, run the CLI from the same tag (`pip install` the same requirement
  into a venv, or `pip install -e ../slopstopper-snyk-safe/cli` from the
  tagged checkout) so `task ss:*` agrees with CI.

## Move the pin

1. Skim the [changelog](../../CHANGELOG.md) for the tags between your pin
   and the target, breaking changes first.
2. Check out the new tag and re-run its installer, so the workflows, shims
   and composite actions match the CLI they will call:

   ```bash
   git -C ../slopstopper-snyk-safe fetch --tags
   git -C ../slopstopper-snyk-safe checkout vX.Y.Z
   bash ../slopstopper-snyk-safe/install.sh
   ```

3. Bump `cli.ref` in `.slopstopper.yml` to `vX.Y.Z` (the installer leaves
   an existing config alone, so this edit is yours).
4. Install the same tag locally and drive `task ss:hygiene:test` and
   `task ss:security:scan` green before pushing. The
   [`slopstopper-install`](../../.claude/skills/slopstopper-install/SKILL.md)
   skill's local-verify loop covers this.
5. Commit `.slopstopper.yml` with the refreshed workflows so CI installs the
   new version in the same change.

For an internal index, bump `SLOPSTOPPER_CLI_SPEC` instead of `cli.ref`,
after publishing the wheel built from the new tag.

## Recover from drift

If `slopstopper --version` locally no longer matches `cli.ref`, reinstall
from the pinned tag:

```bash
pip install --force-reinstall "slopstopper-cli @ git+https://github.com/dataGriff/slopstopper-snyk-safe.git@v0.1.0#subdirectory=cli"
```

Do not `pip install` an unpinned or newer version by hand to "fix" a check:
that drifts you off the committed pin without updating CI, and a check that
passes locally and fails in CI (or the reverse) is the symptom. The scanner
versions (`snyk`, `gitleaks`) are separate pins in `mise.toml`; `mise install`
restores those.

## A pin that CI refuses

`ss-setup` fails with `slopstopper-cli is not pinned` when `cli.source` or
`cli.ref` is missing, and with `cli.source must be an https:// or ssh git URL`
when the source is not a git URL. A `ref` that is a branch installs whatever
that branch currently holds, which defeats the pin; use a tag.
