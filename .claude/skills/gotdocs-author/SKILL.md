---
name: gotdocs-author
description: Write a new gotdocs document - a reference doc, runbook, onboarding guide or dependency note - scaffolded with `bin/gotdocs new <type> <id>` and filled to the format's quality bar. Use when the user says "write a runbook for X", "document this service", "add an onboarding doc", "we need a doc for this dependency", "add a gotdocs doc", "document how this works", or when gotdocs-audit or gotdocs-update identifies a gap that needs a new document.
---

# gotdocs-author

Write one document that is true, discriminative and correctly scoped. One doc per subject.
If you are writing "or, alternatively..." you are writing two documents.

## 1. Pick the type

| Question the reader has | type | Written to |
| --- | --- | --- |
| "How does this work / what may I rely on?" | `doc` | `docs/<id>.md` |
| "It is broken right now, what do I do?" | `runbook` | `runbooks/<id>.md` |
| "I am new / I need this running locally" | `onboarding` | `onboarding/<id>.md` |
| "What is this external thing and what happens when it fails?" | `dependency` | `dependencies/<id>.md` |

A `doc` explains steady state. A `runbook` is written for 3am, for someone who did not build
this, possibly an agent. Do not merge them: a runbook padded with architecture is a runbook
nobody can execute under pressure.

## 2. Check it does not already exist

```sh
grep -n "" .gotdocs/INDEX.md | head -60
bin/gotdocs impacted <the-code-path>          # what already claims this code
grep -ril "<subject keyword>" docs runbooks onboarding dependencies
```

If a doc already covers the subject, extend it instead. Two docs on one subject is how both
get half-maintained.

## 3. Scaffold

```sh
bin/gotdocs new runbook checkout-5xx-spike \
  --title "Runbook: Checkout Returning 5xx" \
  --covers 'src/payments/**' --covers 'src/api/routes/checkout.py'
```

- `id` is kebab-case, unique repo-wide, `^[a-z0-9][a-z0-9-]*$`, <= 64 chars. It is the handle
  in `bin/gotdocs verify <id>` and in every finding, so renaming it later breaks citations.
  Name runbooks after the **symptom** (`checkout-5xx-spike`), not the cause
  (`redis-connection-pool-exhausted`) - at 3am you know the symptom.
- Quote every glob so the shell does not expand it.
- Exit 2 means the id is malformed, taken, or the file exists.

Never hand-write frontmatter. The scaffold gets the required fields, the date and the shape
right.

## 4. Write the `summary` first

`summary` is the only line an agent sees in `.gotdocs/INDEX.md` before deciding whether to
spend tokens opening the file. It is a routing key, not a description. One sentence,
<= 200 chars, must be **discriminative**: after reading it, a reader knows whether this is
the file they want and, just as importantly, that the neighbouring docs are not.

| Bad | Why | Good |
| --- | --- | --- |
| "Documentation for the payments service." | Says nothing the filename did not. | "How checkout charges, retries and refunds a card, including the idempotency-key contract and the 3DS fallback path." |
| "Runbook for API issues." | Which issues? There are twelve. | "Checkout returns 5xx while other routes are healthy - usually Stripe webhook backlog or an exhausted DB connection pool." |
| "Notes about Redis." | Not routable. | "Redis 7 cluster backing session storage and the rate limiter: what breaks when it is down, failover behaviour, and the eviction policy." |
| "Getting started." | Which surface? | "Get the API running locally on macOS/Linux in ~20 minutes: toolchain, .env, docker-compose deps, migrations, and the smoke test that proves it works." |

Test: put your summary next to the other summaries for the same subsystem in `INDEX.md`. If
you cannot tell them apart, it is not discriminative.

## 5. Choose `covers`

`covers` is the claim *"if these files change, statements in this document may become
false."*

1. List the code whose behaviour the prose asserts.
2. Narrowest glob that still spans the whole interface.
3. Measure how often it will fire — with gotdocs' matcher, never with a git pathspec (see
   *Measuring churn* below).
4. Several precise patterns beat one wide one. Config/schema/interface files are the highest
   value entries: rare changes, always doc-invalidating. Package manifests
   (`package.json`, `go.mod`) are not: they take version bumps, which invalidate nothing.
5. Runbooks cover the thing that breaks, not the alerting that reports it.
6. Exclude tests unless you are documenting the tests.
7. `covers: []` is correct for docs about something outside the repo. Do not fake a glob.

Confirm: `bin/gotdocs impacted <a-file-you-expect>` lists your new doc.

### Measuring churn

`git log -- '<glob>'` measures a *different* glob: git pathspecs and the gotdocs dialect
disagree in both directions (`server.ts` matches `src/api/server.ts` in gotdocs and nothing
in git; `src/*.py` matches nothing in gotdocs and everything under `src/` in git). Ask
gotdocs which touched files the doc claims, then count commits with literal paths. Run from
the repo toplevel, after the doc exists and `bin/gotdocs index` has been run:

```sh
rm -f /tmp/gd_log /tmp/gd_touched      # `noclobber` shells refuse to overwrite these
DOC=checkout-5xx-spike
WINDOW='6 months ago'
git log --since="$WINDOW" --pretty=format:'@@%h' --name-only > /tmp/gd_log
grep -v '^@@' /tmp/gd_log | sort -u > /tmp/gd_touched
python3 - "$DOC" <<'PY'
import json, subprocess, sys
doc = sys.argv[1]
paths = [p for p in open("/tmp/gd_touched").read().split("\n") if p]
claimed = set()
for i in range(0, len(paths), 400):
    r = subprocess.run(["bin/gotdocs", "impacted", "--json"] + paths[i:i + 400],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("impacted failed: " + (r.stderr.strip() or r.stdout.strip()))
    claimed |= {p["path"] for p in json.loads(r.stdout)["paths"]
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
print("%s: claims %d files, HITS=%d TOTAL=%d" % (doc, len(claimed), hits, total))
PY
```

Verdict, in order. `TOTAL < 20` — no gate fires: too little history, and the initial-import
commit adds a hit to every candidate glob. Record the counts, keep the narrowest glob that
spans the interface, do not split. **Too broad** iff `TOTAL >= 20` and `HITS > TOTAL / 3` —
split the doc or narrow to the files whose behaviour the prose asserts. **Too narrow** iff
`HITS == 0` and `TOTAL >= 20` — the code is dead, the path is wrong, or the pattern was
written in git's dialect. Otherwise it is fine; at `TOTAL >= 50` the comfortable zone is
roughly one commit in twenty. Use the ratio to choose between two candidate globs, never as
a gate that sends you back into splitting.

Glob dialect: `*` does not cross `/`, `**` does, `a/**` excludes `a` itself, trailing `/`
means the dir and everything under it, a pattern with no `/` matches the basename at any
depth. No braces, no `!` negation.

## 6. Fill the template

The template body is the outline; keep the headings, delete the instructional comment block.
Everything you assert must be checkable against something in `covers` - **read the code
before you write the sentence.** A plausible doc is worse than no doc.

### A good runbook

The spine, in order, is non-negotiable:

1. **Symptom** - what the pager or the user actually reports. Quote the literal alert name
   and error string. Title the file by this, not by the cause.
2. **Confirm it is this** - one or two commands that distinguish it from the runbooks it is
   confused with. State "it is this if <literal output>" *and* "it is NOT this if <output> ->
   go to `runbooks/<other>.md`".
3. **Impact** - who is hurt, whether data is at risk, whether it self-heals, when to page.
4. **What to check, in order** - ordered steps, each with a copy-pasteable command, what good
   output looks like, what bad output looks like, and where to jump when it is bad. Order by
   likelihood x cheapness, not by architecture layer. Never write "check the logs" - write the
   query.
5. **Remediation** - one subsection per cause. Fastest safe mitigation first. Mark anything
   destructive or irreversible in **bold** with its blast radius. Say whether re-running it is
   safe.
6. **Escalate** - the trigger (mitigation has not moved the symptom in N minutes; correctness
   at risk; you lack a credential), who, which channel, and what to hand over.
7. **Verify resolved** - the commands and the numbers that mean it is over: alert clear for N
   minutes, metric back inside a stated range, backlog at normal depth, no new errors matching
   the symptom string. Plus the follow-up ticket if the fix was temporary.

Signs it is not a good runbook: no literal commands; no bad-output examples; "investigate the
issue"; covering three symptoms; no escalation trigger; no definition of resolved; steps that
require knowledge only the author has.

### A good reference doc

- **What it does** and why it exists, for someone who has never seen the repo.
- **How it fits**: upstream callers, downstream dependencies, the state it owns. Name real
  modules, tables, topics, queues.
- **Key behaviour**: the rules not obvious from any single file - ordering, idempotency,
  retries, validation, limits, timeouts, concurrency.
- **Interfaces**: the public surface and, explicitly, what is internal and may change.
- **Configuration**: env vars and flags, defaults, and the blast radius of getting one wrong.
- **Failure modes**, each linked to its runbook.
- **Decisions and trade-offs**: what was rejected and why, so nobody re-litigates or
  accidentally undoes it.
- **Gotchas**: the sharp edges, stated bluntly.

Signs it is not a good doc: it narrates the code line-by-line; it duplicates a dependency doc
instead of linking it; every claim is unfalsifiable; it has no failure modes section.

### A good onboarding doc

Ordered, copy-pasteable, and **actually executed by you before you ship it**: prerequisites
with versions, clone, install, config/`.env`, dependencies (docker-compose etc.), migrations,
run, and a smoke test whose expected output is quoted. Include a "when it does not work"
section with the three failures newcomers actually hit. State the expected total time. Cover
build/config files, so a change to `Dockerfile` or `package.json` marks it stale.

### A good dependency doc

What it is and the version/tier, what in this repo talks to it (with paths), the account or
credential path, limits and quotas that will be hit, what breaks when it is down and whether
there is a fallback, cost/pricing shape if it matters, the upgrade or migration story, and
the runbook for its outage. Cover the client code and pinning file, not the vendor.

## 7. Land it

```sh
bin/gotdocs lint                 # must be clean
bin/gotdocs index                # regenerate index.json + INDEX.md
bin/gotdocs impacted <file>      # confirm the new doc claims the code you meant
git add <new-doc> .gotdocs/index.json .gotdocs/INDEX.md
```

Status: `draft` while any claim is unverified; `current` only when you have checked every
claim against the code. Leave `verified_at` at the scaffolded value unless you read the doc
against the current sha, then `bin/gotdocs verify <id>`.

Cross-link both directions: the doc links its runbooks, the runbook links the doc. A link
that only goes one way gets missed at 3am.

Report: path, id, summary, covers with measured churn, and every claim you could not verify.

## Common mistakes

- A generic `summary` - the doc is then invisible to routing, no matter how good the body is.
- Naming a runbook after the cause instead of the symptom.
- One runbook covering several symptoms.
- "Check the logs" / "investigate the error" instead of the literal command and its expected
  output.
- Writing plausible behaviour from the function names instead of reading the code.
- `covers: src/**`, which makes the doc fire on every commit until people stop reading
  findings.
- Shipping an onboarding doc you did not run end to end.
- Forgetting `bin/gotdocs index`, which leaves an `index_out_of_date` finding.
- Marking `status: current` on a doc with unverified claims.
- Duplicating a dependency doc's content inside a reference doc instead of linking it.
