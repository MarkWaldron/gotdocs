---
name: gotdocs-audit
description: Audit a repo's gotdocs coverage and return a prioritized gap list - code areas no doc's `covers` matches, `covers` globs that match nothing (rotted), docs still marked draft or deprecated, docs whose `verified_at` is far behind HEAD, and known-pitfall areas with no runbook - then offer to author the top gaps. Use when the user says "audit our docs", "what documentation are we missing", "which docs are out of date or rotten", "do we have runbooks for this", "gotdocs coverage report", "check our doc health", or as step 7 of installing gotdocs to seed the initial doc set.
---

# gotdocs-audit

Find what is undocumented, what is documented but rotted, and what will hurt at 3am. Output
a ranked list with an effort estimate per item, then offer to write the top few.

Read-only until the user picks something. Do not author docs during the audit.

## 1. Baseline

```sh
bin/gotdocs status
bin/gotdocs lint --json | python3 -m json.tool | head -40
python3 -c "import json;d=json.load(open('.gotdocs/index.json'));print(len(d['docs']),'docs')"
git log --oneline | wc -l
git log --since='6 months ago' --oneline | wc -l
```

If `lint` is not clean, report the lint errors as gap #0 - the index is derived from
frontmatter and nothing below is trustworthy until it parses.

## 2. Uncovered code (the big one)

Which tracked, non-ignored files match no doc's `covers`? Weight by churn, because an
undocumented file nobody touches is not urgent and an undocumented file changed 40 times
this quarter is.

Write this helper first — steps 2 and 3 both use it. It batches (no `ARG_MAX` ceiling, works
on a 50k-file repo), reads paths on stdin (paths with spaces survive), and uses no `xargs`
flags that BSD/macOS lacks:

```sh
rm -f /tmp/gd_impacted.py /tmp/gd_impacted.json   # `noclobber` shells refuse to overwrite
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

```sh
git ls-files | python3 /tmp/gd_impacted.py /tmp/gd_impacted.json

python3 - <<'PY'
import json, subprocess, collections, os
imp = json.load(open('/tmp/gd_impacted.json'))
uncovered = [e['path'] for e in imp['paths']
             if not e.get('ignored') and not e.get('doc_path') and not e.get('docs')]
log = subprocess.run(['git','log','--since=6 months ago','--pretty=format:','--name-only'],
                     capture_output=True, text=True).stdout.split('\n')
churn = collections.Counter(p for p in log if p)
dirs = collections.Counter()
for p in uncovered:
    dirs[os.path.dirname(p) or '.'] += churn.get(p, 0)
print(f"{len(uncovered)} uncovered files of {len(imp['paths'])}")
for d, c in dirs.most_common(20):
    n = sum(1 for p in uncovered if (os.path.dirname(p) or '.') == d)
    print(f"  {c:5d} churn  {n:3d} files  {d}/")
PY
```

If the vendored gotdocs tooling (`tools/gotdocs/**`, `bin/gotdocs`, `.gotdocs/**`,
`.claude/**`) shows up as uncovered, that is an `ignore` gap, not a doc gap - report it once
as a one-line config fix and drop those files from the ranking.

Report **directories/subsystems**, never individual files - "no doc covers `src/payments/`
(9 files, 74 commits in 6 months)" is actionable; a list of 300 filenames is not. If
`require_coverage` is `true`, `check --json` also emits these as `uncovered` findings; use
them as a cross-check.

Rank an uncovered area higher when it: has high churn, is an external interface (HTTP routes,
CLI, public API, message schemas), owns persistent state, is on the money path, or is the
thing new hires are told to be careful with.

## 3. Rotted `covers` (globs matching nothing)

A doc whose `covers` matches zero tracked files is describing code that moved or died. `lint`
does not report this - it is legal - so check it explicitly.

Decide it with gotdocs' matcher, not a reimplementation of it: `impacted --json` reports, per
path, which of a doc's `covers` patterns matched (`docs[].matched`). Any pattern that never
appears in that output over every tracked file matches nothing.

```sh
python3 - <<'PY'
import json
imp = json.load(open('/tmp/gd_impacted.json'))          # from step 2
live = {}
for e in imp['paths']:
    for d in e['docs']:
        live.setdefault(d['doc_id'], set()).update(d['matched'])
for doc in json.load(open('.gotdocs/index.json'))['docs']:
    dead = [g for g in doc.get('covers', []) if g not in live.get(doc['id'], ())]
    if dead:
        print("%-32s %-40s dead globs: %s" % (doc['id'], doc['path'], dead))
PY
```

A dead glob is one of four things. Check the fourth **first**, because it is the most common
one and the file it names plainly exists, so "fix the glob" is the wrong advice:

1. **The glob names a path inside a doc root** (`decisions/**`, `docs/agent-workflow.md`,
   `.gotdocs/index.json`). Structurally inert: `check` classifies paths under a root as doc
   paths and never as code paths, so one doc's `covers` can never make another doc impacted
   (`docs/doc-format.md`, "changed code path"). Not a rot bug — the glob simply cannot ever
   fire. Report it as "remove this entry, it does nothing", not as a broken pattern.
2. **A rename the doc missed** — fix the glob.
3. **A deleted subsystem** — the doc should be deleted or deprecated.
4. **A pattern written in git's pathspec dialect** rather than gotdocs' (`src/*.py` matches
   nothing here — `*` does not cross `/`).

Cases 2–4 mean the doc has been silently unenforced since it broke — high priority, re-read
the whole doc, not just the frontmatter.

One more nuance: a glob whose only matches are `ignore`d paths also shows up here, because
gotdocs never attributes an ignored path to a doc. That is still a real finding — the doc can
never be marked stale — but the fix is the `ignore` list, not the doc.

To separate case 1 mechanically, compare each `covers` entry against the configured **roots**
(not `.gotdocs/` — files there are ordinary code paths and do make docs impacted):

```sh
python3 - <<'PY'
import json
cfg = json.load(open('.gotdocs/config.json'))
roots = tuple(r.rstrip('/') + '/' for r in cfg.get('roots') or ['docs'])
for doc in json.load(open('.gotdocs/index.json'))['docs']:
    for g in doc.get('covers', []):
        if g.startswith(roots):
            print("%-40s inert (inside a doc root): %s" % (doc['id'], g))
PY
```

Anything it prints can never make its document stale. Confirm one before reporting the set:
`bin/gotdocs impacted <a file the glob matches> --json` shows `"doc_path": true` and an empty
`docs` list.

Also flag docs with `covers: []` that are *not* about something external - that is usually an
unfinished migration, not a deliberate choice.

## 4. Lifecycle: draft, deprecated, and unverified

```sh
python3 - <<'PY'
import json, subprocess, datetime
docs = json.load(open('.gotdocs/index.json'))['docs']
head = subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True).stdout.strip()
today = datetime.date.today()
for d in docs:
    flags = []
    # `current` is the healthy status for a doc; `accepted` is the healthy
    # terminal status for a decision record. Flagging `accepted` reports every
    # ADR in the repo as a gap.
    healthy = {'decision': ('accepted', 'proposed'), None: ('current',)}
    ok = healthy.get(d.get('type'), ('current',))
    if d.get('status') not in ok: flags.append(d.get('status'))
    v = d.get('verified_at')
    if not v or set(v) == {'0'}:
        flags.append('never-verified')
    else:
        n = subprocess.run(['git','rev-list','--count',f'{v}..HEAD'],capture_output=True,text=True)
        behind = n.stdout.strip() if n.returncode == 0 else '?'
        flags.append(f'{behind} commits behind')
        if n.returncode != 0: flags.append('verified_at sha not in history')
    try:
        age = (today - datetime.date.fromisoformat(d['updated'])).days
        if age > 180: flags.append(f'updated {age}d ago')
    except Exception: flags.append('bad updated date')
    if flags: print(f"{d['id']:32s} {d['path']:40s} {', '.join(str(f) for f in flags)}")
PY
```

Interpret, do not just dump:

| Signal | What it means | Priority |
| --- | --- | --- |
| `status: draft`, old `updated` | Abandoned mid-write. Finish it or delete it - a half doc that routes agents to itself is worse than none. | high if it covers live code |
| `status: deprecated`, still present | Should have been deleted. Confirm the replacement exists, then delete. | medium |
| `status: rejected` or `superseded` on a decision | Normal history, not a gap. `why` excludes both. Report one only if `bin/gotdocs lint` also complains that its `superseded_by` chain reaches nothing in force. | none |
| `never-verified` | Nobody has ever asserted it matches the code. | high for `current` docs |
| Far behind HEAD **and** its covered code changed | Enforcement was bypassed (skip token, direct pushes) or the glob is dead. Cross-check with step 3. | high |
| Far behind HEAD but covered code is untouched | Fine. Stable code, stable doc. Not a gap. | none |

"Commits behind HEAD" alone is not staleness - the count only matters relative to whether the
covered code moved. Always report the pair.

## 5. Pitfalls with no runbook

Where does this repo hurt, and is there a runbook for it? Gather evidence, then match against
`runbooks/`.

```sh
grep -rniE "(TODO|FIXME|HACK|XXX|known issue|be careful|do not|gotcha|flaky|race|deadlock)" \
  --include='*.*' src lib app services 2>/dev/null | head -40
git log --oneline -i -E --grep='(hotfix|incident|revert|outage|rollback|postmortem)' -30
grep -rniE "(retry|timeout|circuit|dead.?letter|backoff|rate.?limit|migration|backfill)" \
  --include='*.*' src lib app services 2>/dev/null | wc -l
ls runbooks/ 2>/dev/null
```

Then check each candidate symptom against existing runbooks:

```sh
bin/gotdocs impacted <the-fragile-file> --json | python3 -c \
  'import json,sys; print([d["doc_id"] for e in json.load(sys.stdin)["paths"] for d in e["docs"]])'
```

A subsystem with a `doc` but no `runbook` is a gap whenever it can page someone. High-signal
areas that almost always need one and usually lack one: anything with retries or a
dead-letter path, database migrations and backfills, auth/session expiry, payment or billing
webhooks, queue consumers, cron/scheduled jobs, rate limits and quotas, deploys and
rollbacks, secret/certificate rotation, and every external dependency in `dependencies/`
without a matching outage runbook.

Also check the inverse: `runbooks/` entries whose symptom no longer exists.

## 6. Onboarding and dependency coverage

- Is there an `onboarding/` doc that gets a laptop from clone to a passing smoke test? If it
  exists, does it cover the build files (`Makefile`, `package.json`, `Dockerfile`,
  `.env.example`, CI workflow) so it goes stale when they change? An onboarding doc with no
  `covers` on build files is guaranteed to rot.
- Every external service the repo cannot run without should have a `dependencies/` doc. Find
  them: `grep -rhoE "https?://[a-z0-9.-]+" --include='*.*' src lib app | sort -u | head -30`,
  plus the lockfile's top-level deps and every `*_URL` / `*_API_KEY` in `.env.example`.

## 7. Report

Emit a single prioritized table. Rank by **blast radius x churn x absence**, not by category.

```
gotdocs audit - <repo> @ <short-sha>
14 docs indexed | 312 files tracked | 41% of churn-weighted code uncovered

#  Pri  Gap                                                        Type       Effort
1  P0   src/payments/** (9 files, 74 commits/6mo) - no doc         doc        ~40 min
2  P0   runbooks/ has nothing for webhook dead-letter growth       runbook    ~30 min
3  P1   docs/api.md covers 'src/handlers/**' - matches 0 files     fix+reread ~20 min
4  P1   docs/auth.md never verified, covers code with 22 commits   verify/fix ~15 min
5  P2   onboarding/local-setup.md has no covers on Dockerfile      1-line fix ~2 min
6  P2   docs/legacy-queue.md status: deprecated, still present     delete     ~5 min
```

For each row give one line of evidence (the count, the dead glob, the commit range) so the
user can check your ranking. Then offer explicitly:

> Want me to write #1 and #2? I'd start with the webhook dead-letter runbook - it is the one
> that pages someone.

On yes, hand each item to the right skill: new docs -> **gotdocs-author**; stale/unverified
docs -> **gotdocs-update**; dead globs -> fix `covers`, re-read the doc, then
`bin/gotdocs index`.

## Common mistakes

- Listing 300 uncovered files instead of 8 uncovered subsystems.
- Ranking by file count instead of churn x blast radius.
- Reporting "N commits behind HEAD" as staleness without checking whether the covered code
  actually changed. Stable code with an old `verified_at` is not a gap.
- Treating `covers: []` as automatically wrong - it is correct for docs about external things.
- Treating a `covers` glob that matches nothing as a lint error. It is legal; it is a
  judgement call, and the judgement is usually "the code was renamed".
- Auditing and authoring in the same pass. Produce the ranked list, get a choice, then write.
- Recommending `require_coverage: true` before coverage is good. It turns every file into a
  finding and people stop reading findings.
- Padding the list with P3 items. Ten real gaps beat sixty.
