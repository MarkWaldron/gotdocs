# Repo profiles: starting points for `roots`, `ignore` and `covers`

Read this during step 1/3/5 of gotdocs-install. These are starting points to adapt against
the actual `git ls-files` output, not defaults to paste blindly. Detect the profile, then
confirm each path exists before putting it in config.

## Detection

| Marker file | Profile |
| --- | --- |
| `package.json` + `tsconfig.json` | TypeScript / Node |
| `package.json` + `next.config.*` / `vite.config.*` | JS frontend |
| `pyproject.toml`, `setup.py`, `requirements.txt` | Python |
| `go.mod` | Go |
| `Cargo.toml` | Rust |
| `pom.xml`, `build.gradle*` | JVM |
| `Gemfile` | Ruby |
| `*.csproj`, `*.sln` | .NET |
| `composer.json` | PHP |
| `pubspec.yaml` | Dart/Flutter |
| `Chart.yaml`, `*.tf`, `k8s/`, `helm/` | Infra (often layered on one of the above) |
| several of the above in `packages/*`, `apps/*`, `services/*` | Monorepo |

## Per-profile source roots, extra ignores, high-value `covers` targets

### TypeScript / Node
- source: `src/**`, `lib/**`, `apps/*/src/**`, `packages/*/src/**`
- extra ignore: `**/dist/**`, `**/.next/**`, `**/storybook-static/**`, `**/*.d.ts` (only if
  generated), `**/__snapshots__/**`
- high-value covers: `src/api/routes/**`, `prisma/schema.prisma`, `src/db/migrations/**`
  (hand-written only), `openapi.yaml`, `package.json` (for a dependency doc),
  `src/config/**`, `Dockerfile`

### Python
- source: `src/**`, `<pkg>/**`, `app/**`
- extra ignore: `**/__pycache__/**`, `**/*.egg-info/**`, `.tox/**`, `docs/_build/**`
- high-value covers: `<pkg>/api/**`, `<pkg>/models.py`, `alembic/versions/**` (only if
  hand-edited), `pyproject.toml`, `settings.py`/`config.py`, `tasks/**` (celery), `Dockerfile`

### Go
- source: `cmd/**`, `internal/**`, `pkg/**`
- extra ignore: `vendor/**`, `**/*.pb.go`, `**/*_gen.go`, `**/mocks/**`
- high-value covers: `cmd/<binary>/**`, `internal/<domain>/**`, `api/**` (proto/openapi),
  `go.mod` (dependency doc), `deploy/**`

### Rust
- source: `src/**`, `crates/*/src/**`
- extra ignore: `target/**`, `**/*.rs.bk`
- high-value covers: `crates/<name>/src/lib.rs`, `src/bin/**`, `Cargo.toml`, `migrations/**`

### JVM
- source: `src/main/java/**`, `src/main/kotlin/**`, `*/src/main/**`
- extra ignore: `build/**`, `target/**`, `.gradle/**`, `**/generated/**`
- high-value covers: `**/controller/**`, `**/config/**`, `src/main/resources/application*.yml`,
  `src/main/resources/db/migration/**`, `pom.xml` / `build.gradle*`

### Ruby
- source: `app/**`, `lib/**`
- extra ignore: `vendor/bundle/**`, `tmp/**`, `public/assets/**`, `db/schema.rb` (generated)
- high-value covers: `app/controllers/**`, `app/models/**`, `app/jobs/**`, `config/routes.rb`,
  `config/initializers/**`, `db/migrate/**`

### .NET
- source: `src/**/*.cs`
- extra ignore: `bin/Debug/**`, `bin/Release/**`, `obj/**`, `**/*.designer.cs`
- high-value covers: `src/*/Controllers/**`, `src/*/appsettings*.json`, `src/*/Migrations/**`

### Infra layer (add to whatever else the repo is)
- high-value covers: `terraform/**`, `helm/**`, `k8s/**`, `docker-compose*.yml`,
  `.github/workflows/**`, `Makefile`, `scripts/deploy*`
- these change rarely and almost always invalidate onboarding and deploy runbooks - they are
  the single best return on a `covers` entry

### Monorepo
- keep four top-level roots; do not create `packages/*/docs/`
- name docs by service: `docs/svc-checkout.md`, `runbooks/svc-checkout-5xx.md`
- `covers` per package: `services/checkout/**` plus any shared lib the doc's claims depend on
  (`libs/money/**`), because a change there can falsify the service doc
- add a single `docs/monorepo-layout.md` covering `package.json`, `turbo.json`/`nx.json`,
  `pnpm-workspace.yaml`, `tsconfig.base.json`

## Churn calibration cheat sheet

Measure with the *Measuring churn* block in `SKILL.md` step 5 — it evaluates the glob with
gotdocs' own matcher and then counts commits with literal paths. Do not substitute
`git log -- '<glob>'`: git pathspecs disagree with the gotdocs dialect in both directions
(`server.ts` matches at any depth in gotdocs and nowhere in git; `src/*.py` matches nothing
in gotdocs and the whole subtree in git), so it calibrates a glob you are not installing.

The block prints `HITS` (commits touching a file this doc claims) and `TOTAL` (commits in
the window). Verdict, in order:

| Condition | Verdict | Action |
| --- | --- | --- |
| `TOTAL < 10` | not measurable | record the counts, keep the narrowest glob spanning the interface, re-measure at ~50 commits. Do not split - no glob can go below one commit |
| `HITS >= 4` and `HITS > TOTAL / 3` | too broad | split the doc, or narrow to the files whose behaviour the prose asserts |
| `HITS == 0` and `TOTAL >= 20` | dead or too narrow | widen to the directory, fix the pattern, or confirm the code is gone and mark the doc `deprecated` |
| otherwise | fine | leave it; at `TOTAL >= 50` the comfortable zone is roughly `HITS ~ TOTAL/20` |

A doc whose glob is the whole repo (`src/**` on an architecture doc) will trip the too-broad
row on any repo with history: it is an index, not a doc - give it `covers: []` and write
real docs underneath it.

## Type assignment for migrated prose

| Source content | `type` | `covers` |
| --- | --- | --- |
| "how X works", architecture, API surface | `doc` | the code X is implemented in |
| "when the alert fires, do this" | `runbook` | the code that breaks, not the alerting config |
| "get a laptop running", first PR, conventions | `onboarding` | build/config files: `Makefile`, `package.json`, `Dockerfile`, `.env.example`, CI workflow |
| "we use Postgres/Stripe/Kafka, here is the account and the limits" | `dependency` | the client/config code that talks to it, plus the lockfile if pinning matters |
| dated decision record (ADR) | leave as-is, outside a root | n/a |
| dead system | drop, and say you dropped it | n/a |
