---
name: gotdocs-install
description: Install gotdocs into a repository so its docs, runbooks and onboarding notes stay current automatically - vendors bin/gotdocs, tools/gotdocs/, .gotdocs/, scripts/install-gotdocs.sh and the CI workflow, picks roots/ignore/covers globs for that repo, migrates existing README/docs/CONTRIBUTING/wiki exports into gotdocs frontmatter instead of starting from zero, and stamps verified_at. Use when the user says "set up gotdocs", "add gotdocs to this repo", "install gotdocs", "make this repo keep its docs up to date", "add the docs freshness hook", or points Claude at an existing repo and asks it to adopt this documentation system.
---

# gotdocs-install

Turn a repo that has scattered, rotting docs into one where `bin/gotdocs check` blocks a
change whose documentation is now wrong. The end state: vendored CLI, installed pre-commit
hook, CI job, a `.gotdocs/INDEX.md` an agent reads first, and a starting doc set migrated
from whatever docs already exist.

Never start the target repo from an empty `docs/`. Existing prose is the most valuable
input you have; migrate it.

## 0. Preconditions

```sh
git -C <target> rev-parse --show-toplevel   # must be a git repo
python3 --version                           # must be >= 3.9
```

If either fails, stop and report. Do not install into a non-repo. Everything below uses
paths relative to the target repo toplevel; `cd` there first.

Identify the **gotdocs source** — the checkout of the gotdocs repo you copy from. If the
user did not name one, ask for the path once. Do not reconstruct the CLI by hand.

## 1. Profile the repo

Run these and keep the output; steps 3 and 5 depend on it.

```sh
git ls-files | head -200
git ls-files | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -30   # dir weight
git ls-files | grep -Ei '(^|/)(readme|contributing|architecture|adr|runbook|docs?)' -
git log --since='6 months ago' --name-only --pretty=format: | sort | uniq -c | sort -rn | head -30
```

Record: primary language(s), source root(s), test dirs, build/vendor dirs, deploy/infra
dirs, the 20 highest-churn paths, and every existing prose file. See
`references/repo-profiles.md` for per-language starting points for `roots`, `ignore` and
`covers`.

## 2. Vendor the files

Copy from the gotdocs source, preserving the executable bits:

Run it in a subshell so `set -e` cannot kill your own shell, and set `SRC` once. Nothing in
the block is bash- or zsh-specific; `sh -c` runs it unchanged.

```sh
SRC=<absolute path to the gotdocs checkout>   # set outside the subshell; later steps reuse it
(
set -e   # a failed cp must stop the block, not scroll past
mkdir -p bin tools .gotdocs/hooks scripts .github/workflows .claude/skills
rm -rf tools/gotdocs .gotdocs/templates            # replace these trees, never nest into them
find .claude/skills -maxdepth 1 -name 'gotdocs-*' -exec rm -rf {} +   # not a shell glob: zsh
                                                                     # aborts on no matches
cp    "$SRC"/bin/gotdocs                    bin/gotdocs
cp -R "$SRC"/tools/gotdocs                  tools/gotdocs
cp -R "$SRC"/.gotdocs/templates             .gotdocs/templates
cp    "$SRC"/.gotdocs/config.json           .gotdocs/config.json
cp    "$SRC"/.gotdocs/schema.json           .gotdocs/schema.json
cp    "$SRC"/.gotdocs/README.md             .gotdocs/README.md
cp    "$SRC"/.gotdocs/hooks/pre-commit      .gotdocs/hooks/pre-commit
cp    "$SRC"/.gotdocs/hooks/pre-push        .gotdocs/hooks/pre-push
cp    "$SRC"/scripts/install-gotdocs.sh     scripts/install-gotdocs.sh
cp    "$SRC"/scripts/uninstall-gotdocs.sh   scripts/uninstall-gotdocs.sh
cp    "$SRC"/.github/workflows/gotdocs.yml  .github/workflows/gotdocs.yml
cp -R "$SRC"/.claude/skills/gotdocs-*       .claude/skills/
chmod +x bin/gotdocs scripts/install-gotdocs.sh scripts/uninstall-gotdocs.sh \
         .gotdocs/hooks/pre-commit .gotdocs/hooks/pre-push
rm -rf tools/gotdocs/__pycache__
)
```

Then confirm the copies actually landed — `cp` failures are easy to scroll past:

```sh
ls .claude/skills/          # one gotdocs-* directory per skill in the source
ls .gotdocs/hooks/          # pre-commit AND pre-push
ls scripts/                 # install- and uninstall-gotdocs.sh
ls "$SRC"/.claude/skills/ | wc -l ; ls .claude/skills/ | wc -l   # same number
```

Copy the skills with a glob, not four named lines: the source ships more of them over time,
and a skill the target does not have is a `/gotdocs-*` command that does not exist there.
`.claude/skills` must be in the `mkdir -p` list — without it every `cp -R` of a skill fails,
and (without `set -e`) the block still runs to completion and looks like it worked, leaving
a handover that advertises `/gotdocs-update` and a hook that prints it. Both hooks matter:
`scripts/install-gotdocs.sh` installs `pre-commit` *and* `pre-push`, and `.gotdocs/config.json`
ships an `enforce.pre_push` mode. Vendor one and the installer prints
`pre-push: SKIPPED (no source at .gotdocs/hooks/pre-push)`.

**Re-vendoring an existing install** (upgrading the CLI in a repo that already has gotdocs):
run the same block but drop the `.gotdocs/config.json` line — that file holds this repo's
`roots`, `ignore` and enforcement choices, and the source's copy would erase them. Everything
else in the block is safe to overwrite.

Do **not** copy the gotdocs source's own `docs/`, `runbooks/`, `onboarding/` or
`dependencies/` content, and do not copy its `.gotdocs/index.json` or `.gotdocs/INDEX.md` —
those describe gotdocs itself and would be wrong in the target. `.gotdocs/README.md` *is*
copied: it is the frontmatter reference the lint remediation points at.

**`.claude/settings.json`.** If the target has none, copy the source's verbatim:

```sh
mkdir -p .claude && cp "$SRC"/.claude/settings.json .claude/settings.json
python3 -m json.tool .claude/settings.json > /dev/null && echo "settings.json parses"
```

If it already has one, merge only the two gotdocs entries out of the source file into its
`hooks` object — PostToolUse `Edit|Write|MultiEdit` -> `bin/gotdocs impacted`, and
SessionStart -> print the INDEX.md location. Do not retype them; the PostToolUse hook is an
embedded Python program. Never touch the user's `permissions` or `env` blocks. Verify with
`python3 -m json.tool .claude/settings.json`.

**Housekeeping the vendored tree needs.** `tools/gotdocs/tests/` is now part of the target's
tree, so a bare `pytest` at the repo root collects gotdocs' own tests. Add an exclusion
(`norecursedirs = tools/gotdocs` in `pytest.ini`/`setup.cfg`, or `--ignore=tools/gotdocs`),
and add `__pycache__/` to `.gitignore` — it regenerates the moment anything runs.

**Not a GitHub repo?** Skip `.github/` and port the three gates the workflow runs into that
CI system, in this form:

```sh
bin/gotdocs lint
bin/gotdocs check --base "origin/$DEFAULT_BRANCH" --mode error
bin/gotdocs index && git diff --exit-code -- .gotdocs/index.json .gotdocs/INDEX.md
```

There is no `index --check`; freshness is "regenerate, then require no diff". The base must
be the *other side* of the change (`origin/main`, the target branch), not the branch being
built — on a checkout where the default branch is HEAD the diff is empty and the gate is a
permanent no-op. The CI checkout must have full history (`fetch-depth: 0`), or `REF...HEAD`
has no merge base. `--strict` is optional: add it if you want an internal gotdocs error to
fail the build rather than warn.

## 3. Choose `roots` and `ignore`

Edit `.gotdocs/config.json`.

**roots** — default `["docs", "runbooks", "onboarding", "dependencies"]`. Keep it unless
the repo already has an established docs directory; then point at it rather than creating a
second one (e.g. `["documentation", "runbooks", "onboarding", "dependencies"]`). In a
monorepo, prefer four top-level roots over per-package roots: one index is the point.

**ignore** — start from the shipped list, then add from the profile in step 1:
- vendored / generated trees the repo actually has (`third_party/`, `proto/gen/**`,
  `**/migrations/**` when auto-generated, `**/*.pb.*`)
- fixture and snapshot dirs specific to this repo
- anything in the churn list that is machine-written
- **the gotdocs tooling you just vendored**: `bin/gotdocs`, `tools/gotdocs/**`,
  `.gotdocs/**`, `.claude/**`, `scripts/install-gotdocs.sh`, `scripts/uninstall-gotdocs.sh`,
  `.github/workflows/gotdocs.yml` — name those three files, not `scripts/**` or
  `.github/**`, which are the repo's own. Upgrading the CLI is not a change to the code
  this repo's docs describe, and without this every audit reports ~25 vendored files as
  "uncovered". (Not in the gotdocs source repo, where that tree *is* the product.)

Sanity check the result. `impacted --json` is the command that exposes the decision per
path — it has an `ignored` boolean per path; `check --json` returns only
`{ok, mode, findings, summary}` and never says which paths were ignored.

Write this helper once; steps 3 and 5 both use it. It batches, so it works on a repo of any
size and never trips `ARG_MAX`, and it reads paths on stdin, so paths with spaces survive:

```sh
# a shell with `noclobber` set refuses to overwrite these on a second run
rm -f /tmp/gd_impacted.py /tmp/gd_impact.json /tmp/gd_log /tmp/gd_churn.json
cat > /tmp/gd_impacted.py <<'PY'
import json, subprocess, sys
paths = [p for p in sys.stdin.read().split("\n") if p]
out = []
for i in range(0, len(paths), 400):
    r = subprocess.run(["bin/gotdocs", "impacted", "--json"] + paths[i:i + 400],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("impacted failed: " + (r.stderr.strip() or r.stdout.strip()))
    out.extend(json.loads(r.stdout)["paths"])
json.dump({"paths": out}, open(sys.argv[1], "w"))
PY
```

Run it over every file and read the ignore decision back. `--others
--exclude-standard` alongside `--cached` matters here: the tree you vendored in step 2 is not
committed yet, so a bare `git ls-files` reports zero ignored paths and tells you nothing about
the entries you just added.

```sh
git ls-files --cached --others --exclude-standard | python3 /tmp/gd_impacted.py /tmp/gd_impact.json
python3 - <<'PY'
import json
paths = json.load(open("/tmp/gd_impact.json"))["paths"]
ignored = [p["path"] for p in paths if p["ignored"]]
docs    = [p["path"] for p in paths if p.get("doc_path")]
naked   = [p["path"] for p in paths if not p["ignored"] and not p.get("doc_path") and not p["docs"]]
print("ignored (can never mark a doc stale): %d" % len(ignored))
print("\n".join("  " + p for p in ignored[:40]))
print("doc paths: %d   code paths with no doc: %d" % (len(docs), len(naked)))
PY
```

Read the ignored list. Anything hand-written in it is a mistake; anything machine-written
missing from it is a future false positive.

Leave `require_coverage: false`. Turning it on before coverage exists produces a finding for
every file in the repo and is how adoption dies. `enforce.pre_commit` stays `warn`. Leave the
shipped `enforce.ci` value alone at install time; tighten it to `error` once the doc set is
real and lint has been clean for a while.

## 4. Migrate the docs that already exist

For each prose file found in step 1, decide its fate before writing anything:

| Found | Do |
| --- | --- |
| `README.md` | Keep it as the repo README. Lift the architecture/behaviour sections into `docs/<component>.md`; leave a link behind. Never put frontmatter on the root README - it is not under a root. |
| `docs/*.md` without frontmatter | Add frontmatter in place. Same file, same git history, no rewrite. Wins over any other row it also matches: a `docs/deploy.md` runbook stays where it is. |
| `CONTRIBUTING.md` | Leave it at the repo root - GitHub links to that exact path from every new issue and PR. Lift the parts that are really onboarding into `onboarding/<id>.md` and link back. |
| `SETUP.md`, `docs/getting-started.md` | -> `onboarding/<id>.md`. |
| ADRs (`docs/adr/*.md`) | Leave them. They are dated decisions, not live descriptions. Reference them from the doc that covers the same code; do not put `covers` on them unless you also intend to keep them current. |
| Confluence / Notion / wiki export | Triage first: anything that describes code still in the repo becomes a doc; anything describing a dead system is dropped, not migrated. State what you dropped. |
| Existing runbooks / oncall pages | -> `runbooks/<symptom>.md`, one file per symptom. Split "or, if instead..." pages. |
| Vendor/service notes, "we use Stripe/Kafka/Redis" pages | -> `dependencies/<name>.md`. |

Rules while migrating:
1. **Preserve wording.** You are adding frontmatter and fixing what is now false, not
   rewriting someone's doc in your own voice.
2. **Mark uncertainty honestly.** If you cannot confirm a claim against the code, set
   `status: draft` and leave the claim with a short `> Unverified:` note rather than
   deleting it or silently blessing it.
3. **Use `git mv`, and move before you edit.** `git mv old.md onboarding/new.md` and stage
   that first; add the frontmatter as a second step. Move and rewrite in one shot and git
   records delete + add instead of a rename, which is the thing `git mv` was for.
4. **Frontmatter is seven required keys** — `id`, `title`, `type`, `summary`, `covers`,
   `status`, `updated` — plus optional `owners`, `tags`, `verified_at`. `id` is kebab-case
   and unique repo-wide; `type` is `doc` | `runbook` | `onboarding` | `dependency` and must
   match the root the file sits in; `summary` is one sentence under 200 characters;
   `updated` is `YYYY-MM-DD`. Copy the shape from `.gotdocs/templates/<type>.md`; the full
   reference is `.gotdocs/README.md`. Run `bin/gotdocs lint` after every few files.
5. **One doc per subject.** A 4000-line wiki page becomes several docs with distinct
   `covers`, not one doc covering `src/**`.
6. Every migrated file needs a discriminative `summary` — that line is all an agent sees in
   `INDEX.md` before deciding whether to open the file.

Scaffold new files with the CLI so the frontmatter is right:

```sh
bin/gotdocs new doc payments-api --title "Payments API" --covers 'src/payments/**'
bin/gotdocs new runbook checkout-5xx-spike --covers 'src/payments/**'
```

## 5. Choose `covers` globs

`covers` is a claim: *"if any of these files change, statements in this document may no
longer be true."* Both failure modes are expensive, so calibrate against churn, not against
tidiness.

Decision procedure, per doc:

1. Write down the code the prose actually describes. Start from the modules named in the
   doc body.
2. Turn each into the narrowest glob that still covers the whole interface:
   `src/payments/**` for a subsystem doc, `src/payments/webhooks.py` +
   `src/payments/schema.py` for a doc about webhooks specifically.
3. **Measure the churn** with the block under *Measuring churn* below, then apply the rule
   there. This is the step people skip.
4. Prefer several precise patterns over one wide one. `covers` is a list.
5. Exclude tests unless the doc documents the tests. `src/api/**` matches
   `src/api/tests/test_routes.py`; narrow the pattern or ignore the test dir.
6. Interface and schema files are the best `covers` targets: rare changes, and almost
   always doc-invalidating. Add `openapi.yaml`, `schema.sql`, `proto/**`, `Dockerfile`,
   `terraform/**` to the docs that describe them. **Not** package manifests
   (`package.json`, `pyproject.toml`, `go.mod`) on a subsystem doc — they take version
   bumps, which invalidate nothing and are pure noise. The one exception is an onboarding
   doc that quotes the install/build commands out of the manifest: cover it there, accept
   that a version bump marks that one doc impacted, and clear it with
   `bin/gotdocs verify <id>`.
7. Runbooks cover **the thing that breaks**, not the thing that reports the break.
8. `covers: []` is legitimate for docs about something outside the repo (a vendor API, a
   team process). Never invent a glob to look thorough.
9. Overlap is fine and intended. Three docs describing `src/auth/session.py` are three docs
   that may be wrong when it changes.

### Measuring churn

Do **not** measure with `git log -- '<glob>'`. Git pathspecs are not the gotdocs dialect and
disagree in both directions: `server.ts` matches `src/api/server.ts` in gotdocs and nothing
in git, and `src/*.py` matches nothing in gotdocs (`*` does not cross `/`) but everything
under `src/` in git. Measuring with git would calibrate a glob you are not installing.

Measure with gotdocs' own matcher, then count commits using *literal* paths, which both
tools agree on. Needs `/tmp/gd_impacted.py` from step 3; run from the repo toplevel.

```sh
rm -f /tmp/gd_log /tmp/gd_churn.json   # you will run this once per doc; `noclobber` shells
                                       # refuse to overwrite them on the second pass
DOC=payments-api          # the doc id whose covers you are calibrating
WINDOW='6 months ago'
# 1. one marker line per commit, followed by that commit's files
git log --since="$WINDOW" --pretty=format:'@@%h' --name-only > /tmp/gd_log
# 2. which touched files this doc claims — decided by gotdocs itself
grep -v '^@@' /tmp/gd_log | sort -u | python3 /tmp/gd_impacted.py /tmp/gd_churn.json
# 3. commits that touched at least one claimed file, vs commits total
python3 - "$DOC" <<'PY'
import json, sys
doc = sys.argv[1]
claimed = {p["path"] for p in json.load(open("/tmp/gd_churn.json"))["paths"]
           if any(d["doc_id"] == doc for d in p["docs"])}
hits = total = 0
started = hit = False
for line in open("/tmp/gd_log"):
    line = line.rstrip("\n")
    if line.startswith("@@"):
        if started:
            total, hits = total + 1, hits + (1 if hit else 0)
        started, hit = True, False
    elif line in claimed:
        hit = True
if started:
    total, hits = total + 1, hits + (1 if hit else 0)
print("%s: claims %d files, HITS=%d TOTAL=%d (%.0f%%)"
      % (doc, len(claimed), hits, total, 100.0 * hits / max(total, 1)))
PY
```

Repeat with a different `DOC=` per doc; steps 1 and 2 can be reused across docs only if the
`covers` you edited since are re-run through step 2 again.

Then apply this rule to `HITS` and `TOTAL`, in order. It is arithmetic, it needs no
percentage band, and it terminates on a ten-commit repo and a ten-thousand-commit repo alike:

- `TOTAL < 20` — **no gate fires; record the two numbers and move on.** There is not enough
  history to calibrate against, and the initial-import commit touches every file, so it adds
  a hit to every candidate glob and inflates small-repo ratios on its own. Keep the narrowest
  glob that spans the interface and re-measure once the repo has ~50 commits. **Do not split
  docs at this size**: no glob can go below one commit, so splitting cannot move the number
  past the floor, and a follower chasing a ratio here splits until every doc covers one file.
- **Too broad** iff `TOTAL >= 20` **and** `HITS > TOTAL / 3`. Split the doc into narrower docs
  with narrower globs, or drop to the specific files whose behaviour the prose asserts. Alarm
  fatigue is the failure mode that kills adoption. (At `TOTAL == 20` that is 7 hits; the gate
  cannot be tripped by a single unlucky commit.)
- **Too narrow** iff `HITS == 0` **and** `TOTAL >= 20`. The glob is aimed at dead code, or at
  a path that does not exist, or is written in git's dialect rather than gotdocs'. Widen to
  the directory, fix the pattern, or say plainly that the doc describes something no longer
  in the repo.
- Anything else passes — say so and move on. Once `TOTAL >= 50` the comfortable landing zone
  is about one commit in twenty (`HITS` near `TOTAL/20`, the familiar 2-10%); use that to
  choose between two candidate globs, never as a gate that sends you back into splitting.

Record `HITS/TOTAL` per doc; step 8 reports it.

Glob dialect (not fnmatch): `*` does not cross `/`, `**` does — including inside a segment,
so `src/**.py` matches `src/a/b.py` — `a/**` excludes `a` itself, a trailing `/` means the
directory and everything under it, a pattern with no `/` and no `**` matches the basename at
any depth, no brace expansion, no `!` negation. The copy of this reference that exists in
the target repo is `.gotdocs/README.md`; the long form is `docs/doc-format.md` in the
gotdocs source.

Verify each glob resolves to the files you meant:

```sh
bin/gotdocs impacted src/payments/webhooks.py
bin/gotdocs check --paths src/payments/webhooks.py --json | python3 -m json.tool
```

## 6. Install the hook and generate the index

```sh
sh scripts/install-gotdocs.sh
bin/gotdocs lint          # must be clean before anything else
bin/gotdocs index         # writes .gotdocs/index.json + .gotdocs/INDEX.md
bin/gotdocs status
```

Fix every lint error now. `lint` exit 2 means the frontmatter you just wrote is wrong, and
every downstream step reads that frontmatter.

## 7. Seed the doc set

Run the **gotdocs-audit** skill against the repo. It returns a prioritized gap list. Author
the top gaps with the **gotdocs-author** skill, biasing toward:
- the two or three highest-churn source areas with no doc
- one onboarding doc: clone -> build -> test -> run, verified by actually running it
- runbooks for pitfalls the user or the code comments already name
- a `dependencies/` doc for each external service the repo cannot run without

Aim for a first pass of 5-12 docs. A hundred thin docs is worse than eight true ones.

## 8. Stamp and hand over

```sh
# NAME THE DOCS. One id per doc you actually read against the code:
bin/gotdocs verify payments-api checkout-5xx-spike onboarding-local-setup
bin/gotdocs index
bin/gotdocs lint
git add -A
bin/gotdocs check --staged --mode warn    # exactly what the hook will run on their first commit
git status --short
```

Stage first, then `check --staged`. `check --paths $(git ls-files ...)` looks equivalent and is
not: it word-splits on paths containing spaces, and `git ls-files` does not list the docs you
just wrote, so the one thing you most want checked is the thing it skips.

`verify` stamps `verified_at` to the current short sha, which is the assertion "this was
read against the code at this sha". **Never stamp the whole index** — piping every id out of
`index.json` into `verify` is exactly the laundering this step exists to prevent, and it
leaves docs that are simultaneously `status: draft` and stamped. Anything still unread stays
`status: draft` with no `verified_at`.

Flip `status: draft` -> `current` on exactly the docs you stamped, in the same pass. `verify`
does not touch `status`, and a doc that is stamped *and* draft asserts both "I read this
against 531b65e" and "nobody has checked this" — `/gotdocs-audit` reports the pair as a gap.
Do this before `bin/gotdocs index`, so the index and `INDEX.md` carry the final status.

`bin/gotdocs new` writes a `verified_at` of HEAD into the scaffold. For a doc you scaffolded
but did not finish, delete that line before handing over, so `never-verified` detection in
`/gotdocs-audit` still works.

Do not commit. Report to the user: files added, `roots`/`ignore` chosen and why, the doc
inventory with each `covers` and its measured churn (`HITS/TOTAL`), what was migrated vs
dropped, and the remaining gaps ranked.

Tell them the three things they now need to know:
- `bin/gotdocs check --staged` runs on every commit (warn locally, error in CI)
- when it fires, run `/gotdocs-update`
- `.gotdocs/INDEX.md` is the entry point for agents

## Common mistakes

- Copying the gotdocs source's own `docs/` into the target. Those describe gotdocs.
- Starting from an empty docs tree when a README and a wiki export exist.
- `covers: src/**` on an architecture doc. It fires on every commit; people learn to use the
  skip token; gotdocs is now off.
- Setting `require_coverage: true` at install time.
- Setting `enforce.pre_commit: error` on day one. Warn first, error once the doc set is real.
- Stamping `verified_at` on migrated docs nobody read - it launders unverified prose as
  verified.
- Forgetting `chmod +x` on `bin/gotdocs`, both `scripts/*.sh` and both hooks.
- Vendoring `.gotdocs/hooks/pre-commit` without `pre-push`, or copying the skills into a
  `.claude/skills/` that was never created, so every `cp` fails silently and the handover
  advertises a `/gotdocs-update` that is not there.
- Stamping every id in `index.json` with `verify` in step 8, or leaving a stamped doc at
  `status: draft`.
- Measuring churn with `git log -- '<glob>'`, which evaluates a git pathspec, not a gotdocs
  glob, and so calibrates a pattern you are not installing.
- Leaving the vendored `tools/gotdocs/**` and `.gotdocs/**` out of `ignore` in a target repo,
  which makes every audit report ~25 uncovered files that are not the repo's code.
- Sanity-checking `ignore` with a bare `git ls-files` in step 3. The vendored tree is still
  untracked there, so the check reports zero ignored paths and looks like the list is inert.
- Forgetting `bin/gotdocs index` after authoring, which leaves an `index_out_of_date`
  finding on the very first commit.
- Rewriting migrated prose into your own voice instead of preserving it.
