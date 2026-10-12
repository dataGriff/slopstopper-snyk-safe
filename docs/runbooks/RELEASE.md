# Cutting a release

A `slopstopper-cli` release is an annotated git tag of this repository. There is no package registry and no release bot: adopters pin the tag under `cli.ref` in `.slopstopper.yml`, and CI installs the CLI straight from it (`git+<source>@<ref>#subdirectory=cli`). The tag is therefore the release, and cutting one is four edits and a push.

## The flow

1. **Decide the version.** Semantic Versioning, with the pre-1.0 convention that `feat:` commits bump MINOR and `fix:` commits bump PATCH. Read `git log --oneline <last-tag>..main` to see what has landed since the last tag; Conventional Commit prefixes make the bump obvious.
2. **Bump the version in both places.** They must match, and the tag must match them:

   ```bash
   sed -i 's/^version = ".*"/version = "X.Y.Z"/' cli/pyproject.toml
   printf '__version__ = "X.Y.Z"\n' > cli/slopstopper/__init__.py
   ```

3. **Update `CHANGELOG.md`.** Add a `## X.Y.Z (YYYY-MM-DD)` section at the top, grouped the way the previous entries are (features, fixes, breaking changes called out first). Write it for an adopter deciding whether to move their pin: what changed in a check's verdict, a renamed task or workflow, a new config key, a new egress host.
4. **Commit and merge.** `chore(release): X.Y.Z` on a branch, through a PR like any other change, so the hygiene checks and the CLI tests run on it. Squash-merge to `main`.
5. **Tag the merge commit and push the tag:**

   ```bash
   git checkout main && git pull
   git tag -a vX.Y.Z -m "slopstopper-cli X.Y.Z"
   git push origin vX.Y.Z
   ```

   Annotated (`-a`), so the tag records who cut it and when, and `git describe` and the GitHub Releases page treat it as a release. Never move or delete a pushed tag: an adopter's `cli.ref` points at it, and a moved tag silently changes what their CI runs. A mistake gets a new PATCH tag.
6. **Create the GitHub Release** from the tag (`gh release create vX.Y.Z --notes-from-tag`, or paste the changelog section). Optional but worth it: the Releases page is where adopters look, and `gh release download` is how they fetch the SBOM artifact for the tagged commit if they archive it.
7. **Verify** from a clean environment that the tag installs:

   ```bash
   pip install "slopstopper-cli @ git+https://github.com/dataGriff/slopstopper-snyk-safe.git@vX.Y.Z#subdirectory=cli"
   slopstopper --version      # prints X.Y.Z
   ```

## What adopters do next

Nothing happens to an adopter repo until someone moves its pin. The adopter checks out the new tag, re-runs `install.sh` from it (refreshes workflows, shims and composite actions) and bumps `cli.ref`; [UPGRADE_CLI.md](./UPGRADE_CLI.md) is their side of this. Mention breaking changes at the top of the changelog entry, because that is the paragraph they read before bumping.

## Mirrors and internal indexes

An enterprise that mirrors this repository tags the mirror the same way (`git push mirror vX.Y.Z` after the mirror fetches `main`), so `cli.source` can point at the mirror with the same `ref`. One that serves wheels from an internal index builds the wheel from the tag (`git checkout vX.Y.Z && pip wheel ./cli`), uploads it, and adopters set `SLOPSTOPPER_CLI_SPEC` to `slopstopper-cli==X.Y.Z`. Either way the version string inside the package is the one from step 2, so `slopstopper --version` agrees with the tag.

## Checklist

- [ ] `cli/pyproject.toml` `version` and `cli/slopstopper/__init__.py` `__version__` both say `X.Y.Z`
- [ ] `CHANGELOG.md` has a `## X.Y.Z (date)` section and breaking changes are at the top of it
- [ ] `task contributing:test` and `task ss:hygiene:test` are green on the release commit
- [ ] The tag is annotated, named `vX.Y.Z`, on the merge commit on `main`, and pushed
- [ ] A clean `pip install … @vX.Y.Z#subdirectory=cli` prints the version
