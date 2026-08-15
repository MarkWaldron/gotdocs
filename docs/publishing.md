---
id: publishing
title: Publishing — Portability Guarantees, Lint Rules and Export Targets
type: doc
summary: What gotdocs promises about rendering on six static site generators, the fifteen portability lint rules, what `gotdocs export` maps per target, and the h1_in_body tradeoff.
covers:
  - tools/gotdocs/export.py
  - tools/gotdocs/portability.py
owners: ["@mark"]
tags: [publishing, ssg, export, portability, lint]
status: current
updated: 2026-08-15
verified_at: 3d8b6cd
---

# Publishing — Portability Guarantees, Lint Rules and Export Targets

The requirement is blunt: *the host does not matter, the files must render
correctly on most static site generators.* Documents live in the repo and are
read on GitHub every day; publishing them to a docs site later must not require
rewriting them, and must not be a choice that locks the repo into one vendor.

That splits into two halves, and they are independent:

- **Portability** — `bin/gotdocs lint --portability` is a pure analyser. It reads
  documents, reports `(rule, path, line, severity, message, remediation)`, writes
  nothing and touches no git. It tells you a document will render badly *before*
  anybody picks a generator.
- **Export** — `bin/gotdocs export` renders the committed documents into one
  generator's conventions. Also non-destructive with respect to the source: it
  reads the repo and writes a separate output tree.

You can use either without the other. Linting for portability with no intention
of ever publishing is still worth it — a broken relative link is broken on
GitHub too.

## The guarantees

Six targets: **Docusaurus, MkDocs (+Material), Astro Starlight, Jekyll, Hugo,
and plain GitHub.**

1. **A document that passes `lint --portability` renders on all six.** Not
   identically — ordering and chrome differ — but without a build failure and
   without a broken link.
2. **Nothing is lost in export.** Keys a generator has no meaning for are not
   dropped from the *export*, they are moved into `_gotdocs.json` at the root of
   the output tree, with the source path, the output path, the site URL,
   `covers`, `owners`, `status`, `verified_at`, the decision-record fields
   (`symptoms`, `supersedes`, `superseded_by`, `decided_on`) and any extra keys
   under `extra`. A publishing job can still see ownership and coverage.

   Read that precisely: nothing is lost from the *output tree*. It is not a
   passthrough — a key you write in a source document does **not** appear in the
   exported page's frontmatter unless the mapping table below puts it there. See
   [§1 below](#1-frontmatter-keys-are-mapped-not-passed-through).
3. **Export is byte-deterministic.** No timestamp, no head sha, no dictionary
   iteration order anywhere in the output. The same documents produce the same
   bytes on every machine and every run, so an export can be committed or diffed
   in CI. Files whose bytes are unchanged are not rewritten.
4. **The source stays canonical.** Export never edits the documents. Switching
   generators means changing `--target`, not editing 40 files.
5. **False positives are treated as worse than missed issues.** Before any rule
   runs, the scanner masks fenced code blocks (``` and `~~~`, any info string,
   any fence length), indented code blocks, inline code spans including
   multi-line and doubled-backtick spans, and HTML comments. Only surviving prose
   is scanned, and link destinations carrying a URL scheme (`https:`, `mailto:`)
   are never treated as file paths. A portability linter that cries wolf about
   `{` inside a shell snippet gets turned off within a week.

## The lint rules

Fifteen rules. `error` means the document will fail a build or a link is already
broken; `warn` means it builds but renders differently than intended.

| Rule | Severity | Targets | What it catches |
| --- | --- | --- | --- |
| `link-target-missing` | error | all | a relative link points at a file that does not exist in the repository |
| `link-case-mismatch` | error | all | a link differs in case from the file on disk — resolves on macOS/Windows, 404s on a Linux build |
| `link-absolute-path` | error | all | an absolute filesystem path (`/Users/...`, `C:\...`, `file://`) |
| `link-escapes-repo` | error | all | a relative link resolves above the repository root |
| `image-missing` | error | all | a referenced image does not exist |
| `document-unreadable` | error | all | the file is not decodable as UTF-8 text |
| `mdx-unclosed-tag` | error | docusaurus, starlight | an HTML-ish tag in prose is never closed; MDX parses it as JSX and fails the build |
| `mdx-bare-tag` | warn | docusaurus, starlight | a capitalized tag in prose is parsed as an undefined React component |
| `mdx-brace` | warn | docusaurus, starlight | a `{` or `}` in prose is parsed as a JavaScript expression |
| `mdx-html-comment` | warn | docusaurus, starlight | an HTML comment is not valid MDX |
| `link-site-absolute` | warn | all | a root-relative link (`/guide`) resolves against the site root, so it breaks when read on GitHub or under a base path |
| `fence-language-missing` | warn | all | a fenced code block has no language tag, so it is not highlighted |
| `code-block-tab-indent` | warn | all | a code block indented with tabs; tab width differs per generator |
| `h1-count` | warn | all | the number of body H1s does not match `publish.h1_in_body` |
| `frontmatter-reserved-key` | warn | all | a frontmatter key is reserved by a target generator and will be reinterpreted or overwritten |

`link-case-mismatch` is the one that earns its keep. It is invisible to everyone
developing on macOS and breaks the moment the site builds on Linux, which is
where every CI runner lives.

`frontmatter-reserved-key` uses a per-target list of the keys that generator
owns — `sidebar_position`, `slug`, `unlisted` for Docusaurus; `weight`,
`cascade`, `aliases` for Hugo; `permalink`, `layout`, `published` for Jekyll, and
so on. It also flags one gotdocs key by name: `type` collides with Hugo's content
type and would fail that build, which is why the exporter strips it.

Running it:

```text
$ bin/gotdocs lint --portability
gotdocs: no lint errors in 25 documents

gotdocs: no portability warnings

# ...and on a tree that has not been cleaned up yet:
gotdocs: 30 portability warnings (not blocking; re-run with --strict to fail on them)

  docs/guide.md:72: fenced code block has no language tag (fence-language-missing)
    -> add a language after the fence (```sh, ```python, ```text)
```

Warnings by default, exit `0`. `--strict` promotes them to findings and exits
`2`. Narrow with `--targets docusaurus,hugo` or `--rules link-target-missing`;
an unknown name in either is a usage error listing the valid values.

Adopt it in that order: `--rules` for the error-severity link rules first, get
those to zero, then widen. Turning on all fifteen against an existing doc tree
produces a `fence-language-missing` wall that teaches people to ignore the
command.

## `gotdocs export`

```sh
bin/gotdocs export --target hugo --out build/gotdocs-site
```

```text
gotdocs: exported 25 documents for hugo -> build/gotdocs-site
         26 file(s) changed on disk
```

The repo layout is preserved — `docs/cli-reference.md` becomes
`<out>/docs/cli-reference.md` — so the output tree is navigable and the source of
any page is obvious. `_gotdocs.json` sits at the root. `--dry-run` renders and
reports without writing; `--clean` deletes files in the output tree this export
did not produce.

Three things happen to every document.

### 1. Frontmatter keys are mapped, not passed through

Six keys are **stripped** from the output frontmatter for every target and land
in `_gotdocs.json` instead: `id`, `type`, `covers`, `owners`, `status`,
`verified_at`. (`id` is re-emitted for Docusaurus, which has its own `id`
concept.) `status` is not dropped so much as translated — it becomes each
target's draft convention.

| gotdocs key | docusaurus | mkdocs | starlight | jekyll | hugo | github |
| --- | --- | --- | --- | --- | --- | --- |
| `title` | `title` | `title` | `title` | `title` | `title` | body H1 |
| `summary` | `description` | `description` | `description` | `description` | `description` | — |
| `updated` | `last_update.date` | `date` | `lastUpdated` | `date` | `date`, `lastmod` | — |
| `tags` | `tags` | `tags` | — | `tags` | `tags` | — |
| *(derived order)* | `sidebar_position` | — | `sidebar.order` | `nav_order` | `weight` (×10) | — |
| `status: draft` | `draft: true` | — | `draft: true` | `published: false` | `draft: true` | — |
| `status: deprecated` | `unlisted: true` | — | `sidebar.badge: Deprecated` | — | — | — |
| *(also)* | `id`, `slug` | — | — | `layout` | — | — |

Verify the mapping for your version rather than trusting this table:

```sh
bin/gotdocs export --list-targets
```

Two per-target notes worth knowing. MkDocs gets no ordering key at all —
ordering lives in `mkdocs.yml`'s `nav`, not in the page, and inventing a key
there would be noise. Starlight emits only Starlight's own keys, including
dropping `tags`, because Astro validates the content collection schema and an
unknown key is a build error rather than a cosmetic problem.

The **derived order** is per output directory: an explicit integer `order` in the
document's frontmatter wins, and everything else is numbered `1..N` by path with
index pages first. Hugo multiplies by 10 (`weight: 30`) so a human can wedge a
page between two others without renumbering.

### 2. Links are rewritten for the target's URL scheme

Same source line, four different outputs — all from a real export of
`dependencies/git.md`:

| Target | Link style | Result |
| --- | --- | --- |
| docusaurus, mkdocs, github | `md` | `[docs/architecture.md](../docs/architecture.md)` |
| jekyll | `html` | `[docs/architecture.md](../docs/architecture.html)` |
| hugo | `pretty` | `[docs/architecture.md](/docs/architecture/)` |
| starlight | `extensionless` | `[docs/architecture.md](/docs/architecture)` |

Anchors survive: a link to `../runbooks/ci-check-failing.md#4--bad-base-ref` is
rewritten to `../runbooks/ci-check-failing.html#4--bad-base-ref` under Jekyll,
fragment intact. Links that resolve to a file which is *not* an exported
document (a source file, a config) are left relative unless `--source-url` is
set, in which case they are repointed at that base URL. Referenced images are copied into the output tree and
their links repointed.

`--url-prefix` prepends the path the site is served under, for the two
site-absolute styles.

Destinations are re-encoded on the way out. A link is resolved against the
percent-*decoded* path, so `[a](./release%20notes.md)` and
`[a](<./release notes.md>)` both find the same file, and the rewritten
destination is percent-encoded again (`release%20notes.md`). Emitting the decoded
form would end the link — a bare CommonMark destination cannot contain a space.
An ordinary path with nothing to escape is emitted byte-for-byte.

### 3. The body H1 is reconciled

Generators that print the frontmatter `title` render a body `# Title` as a second
title. So for those five targets the leading H1 is stripped — only the *leading*
one; an H1 further down is real content and is left alone. For `github`, which
emits no frontmatter at all, the title has to *be* the H1, so one is inserted
when missing.

Compare the same document exported two ways:

```text
$ bin/gotdocs export --target starlight --out /tmp/gd-starlight
$ head -6 /tmp/gd-starlight/docs/cli-reference.md
---
title: Gotdocs CLI Reference
description: Every gotdocs command, flag and exit code, plus the --json output shape that agents and CI depend on.
sidebar:
  order: 3
lastUpdated: 2026-08-14

$ bin/gotdocs export --target github --out /tmp/gd-github
$ head -2 /tmp/gd-github/docs/cli-reference.md
# Gotdocs CLI Reference
```

## The `publish.h1_in_body` tradeoff

```json
"publish": { "h1_in_body": true }
```

Stated plainly: **you cannot have both a clean GitHub read and a clean
frontmatter-title render without the exporter doing the reconciling, and
`h1_in_body` is you telling the linter which of the two your source files are
written for.**

- **`true` (the default).** Every document carries exactly one `# Title` in the
  body. This is what plain GitHub needs — GitHub renders the frontmatter block as
  a table, or hides it, and without a body H1 the page opens with no heading at
  all. `h1-count` then flags any document with zero H1s, and any document with
  two or more, because generators use the first one as the page title and the
  second reads as a broken outline.
- **`false`.** Documents carry no body H1; the title lives only in gotdocs
  frontmatter. This is right for a repo whose documents are read only through a
  published site, where a body H1 renders *twice* — once from the frontmatter
  title, once from the body. `h1-count` then flags any document that has one.

What it does **not** do: change the export. The exporter reconciles the H1 to
each target regardless of this setting, stripping it for the five
frontmatter-title targets and inserting it for `github`. So a repo with
`h1_in_body: false` still publishes correctly to GitHub-flavoured output, and a
repo with `h1_in_body: true` still publishes correctly to Docusaurus.

The setting is therefore about **the source files and the linter**, not about the
output. Pick `true` unless nobody reads your docs on GitHub — and in this repo,
where the docs are the reference example and are read on GitHub constantly, `true`
is not a close call.

The residual cost of `true` is honest and small: the title is stated twice in
every file, in `title:` and in `# Title`, and they can drift. `bin/gotdocs new`
writes both from the same argument, and nothing keeps them in step afterwards.
If that drift matters more to you than the GitHub read, `false` is the trade.

## Wiring it into CI

There is no shipped publish job — the target repo's own site pipeline owns that.
The two things worth adding:

```sh
bin/gotdocs lint --portability --strict          # gate: will these files publish?
bin/gotdocs export --target hugo --out build/site --clean --dry-run
```

`--dry-run` on a pull request tells you the export still succeeds without
producing an artifact. Add `--strict` to the lint only after the existing
warnings are at zero, or it goes red on day one and gets deleted.

## Related

- [cli-reference.md](cli-reference.md#gotdocs-export) — every `export` and `lint --portability` flag
- [doc-format.md](doc-format.md) — extra frontmatter keys are preserved by `lint`/`index` and carried into `_gotdocs.json`, not into the exported page
- `.gotdocs/README.md` — the `publish` config block, key by key
