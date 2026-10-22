---
name: release
description: Publish a new version of the Eclipse Decky plugin to GitHub Releases. Use when the user asks to release, publish, ship or cut a new version (e.g. "release v0.2.0", "ra bản mới", "release đi"). Covers the version bump, release notes, tag push and checking the Release workflow until the zip is attached.
---

# Releasing Eclipse

Releases are built by GitHub Actions, not on this machine: pushing a tag `v<version>` runs
`.github/workflows/release.yml`, which checks that the tag matches `package.json`, runs the typecheck and the
backend tests, builds the zip with `scripts/package.sh` (frontend + native helpers) and publishes a GitHub
release with `Eclipse-<version>.zip` attached.

The plugin's own update check (`py_modules/eclipse/updates.py`) relies on exactly that shape: the latest
release has a `vX.Y.Z` tag and a `.zip` asset. Never publish a release without the zip.

## Rules

- Pushing a tag publishes to everyone who has the plugin. Do it only when the user asked for a release in
  this conversation, and confirm the version number with them if they did not give one.
- Code, comments, docs and release notes are written in English. Talk to the user in Vietnamese.
- `gh` is not installed. Use `git` for tags and the public GitHub API (no token needed, the repo is public)
  for checking results.
- Do not move or delete a tag that was already pushed unless the user agrees: someone may have installed it.

## Steps

1. **Preflight.** On `master`, in sync with `origin/master`, nothing uncommitted:
   ```bash
   git fetch -q origin && git status -sb && git log --oneline -5
   git tag --sort=-v:refname | head -5
   ```
   If there are uncommitted changes, ask whether they belong in the release before committing anything.

2. **Check locally first** (CI runs the same checks; a failed release costs a tag):
   ```bash
   pnpm typecheck && python3 -m unittest discover -s tests && pnpm package
   ```

3. **Pick the version** (semver, from the last tag): fixes only → patch, new features → minor, breaking
   changes (settings format, install location) → minor while below 1.0. Set it in `package.json` (`version`).
   `plugin.json` has no version.

4. **Write release notes** in `.github/release-notes/v<version>.md` (the workflow uses them as the release
   body; without the file GitHub generates notes from commits). Base them on
   `git log --oneline <last-tag>..HEAD`, grouped like the existing notes: *Play (Moonlight)*, *Share
   (Sunshine)*, *Quick Access menu*, *Fixes*. Mention anything users must do, such as a **Repair** of Sunshine
   for Gaming Mode after a change to its install or helpers.

5. **Commit, push, tag:**
   ```bash
   git add package.json .github/release-notes/v<version>.md
   git commit -m "chore: release v<version>"   # plus the attribution trailer
   git push origin master
   git tag -a v<version> -m "Eclipse <version>"
   git push origin v<version>
   ```

6. **Watch the workflow** until it completes (usually 2–4 minutes). Poll with Python, not `echo | python`
   (`echo` mangles the JSON):
   ```bash
   python3 - <<'EOF'
   import json, time, urllib.request
   def get(url):
       return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "eclipse-release"})))
   for _ in range(40):
       run = get("https://api.github.com/repos/1m93/DeckyEclipse/actions/runs?per_page=1")["workflow_runs"][0]
       print(run["head_branch"], run["status"], run["conclusion"], run["html_url"], flush=True)
       if run["status"] == "completed":
           break
       time.sleep(15)
   rel = get("https://api.github.com/repos/1m93/DeckyEclipse/releases/latest")
   print(rel["tag_name"], [a["name"] for a in rel["assets"]], rel["html_url"])
   EOF
   ```
   Done when the run's conclusion is `success` and the latest release is the new tag with
   `Eclipse-<version>.zip`.

7. **Report** to the user: the release URL, and the install link for Decky
   (`https://github.com/1m93/DeckyEclipse/releases/download/v<version>/Eclipse-<version>.zip`, for
   *Developer → Install plugin from URL*).

## When the workflow fails

Open the run (`html_url`) and read the failed step; job logs need a login, so ask the user to paste them if
the step name is not enough.

- **"Tag … does not match package.json version"**: the bump was not committed before tagging.
- **Typecheck or tests**: reproduce locally with the commands from step 2.
- **Build** (`scripts/package.sh`, zig from PyPI): usually a network hiccup; re-run the job from the
  Actions page, no new tag needed.

After a code fix, ask the user whether to re-tag the same version (delete with
`git push origin :refs/tags/v<version>` and `git tag -d v<version>`, then tag again) or to release the next
patch version instead. Prefer the next patch version if the broken release was already published.
