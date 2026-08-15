"""The core rule: change set -> impacted -> satisfied/stale -> findings.

From docs/architecture.md#the-core-rule-precisely, executed in order:

1. Determine the change set (``--staged``, ``--base REF`` or ``--paths P...``).
2. Split it: a path inside a configured root is a *doc path*; every other path
   is a *code path* unless it matches an ``ignore`` glob.
3. A doc is **impacted** when any code path matches any of its ``covers`` globs.
4. An impacted doc is **satisfied** when its own file is in the change set, or
   when its ``verified_at`` is the head sha of the change set. Otherwise it is
   **stale**.
5. Code paths matching no doc's ``covers`` yield ``uncovered`` findings only
   when ``require_coverage`` is true.
6. Doc-side findings are always reported: ``lint``, ``duplicate_id``,
   ``deprecated_edit`` and ``index_out_of_date``.
7. Everything is skipped when the commit message or ``GOTDOCS_SKIP`` carries the
   skip token.
"""

import os

from . import gitutil
from . import index as index_module
from .errors import EmptyRepoError, UsageError

__all__ = [
    "Finding",
    "CheckResult",
    "KIND_ORDER",
    "SOURCE_STAGED",
    "SOURCE_BASE",
    "SOURCE_PATHS",
    "run_check",
    "impacted_for_paths",
    "skip_requested",
    "sha_satisfies",
]

SOURCE_STAGED = "staged"
SOURCE_BASE = "base"
SOURCE_PATHS = "paths"

KIND_STALE = "stale"
KIND_UNCOVERED = "uncovered"
KIND_LINT = "lint"
KIND_DUPLICATE_ID = "duplicate_id"
KIND_DEPRECATED_EDIT = "deprecated_edit"
KIND_INDEX_OUT_OF_DATE = "index_out_of_date"

# Human output and the --json contract both group findings in this order.
KIND_ORDER = (
    KIND_STALE,
    KIND_UNCOVERED,
    KIND_LINT,
    KIND_DUPLICATE_ID,
    KIND_DEPRECATED_EDIT,
    KIND_INDEX_OUT_OF_DATE,
)

_SKIP_ENV_TRUE = ("1", "true", "yes", "on")


class Finding(object):
    """A structured record: ``{kind, doc_id, path, message, remediation}``."""

    __slots__ = ("kind", "doc_id", "path", "message", "remediation")

    def __init__(self, kind, path, message, remediation, doc_id=None):
        self.kind = kind
        self.doc_id = doc_id
        self.path = path
        self.message = message
        self.remediation = remediation

    def as_dict(self):
        return {
            "kind": self.kind,
            "doc_id": self.doc_id,
            "path": self.path,
            "message": self.message,
            "remediation": self.remediation,
        }

    def sort_key(self):
        try:
            rank = KIND_ORDER.index(self.kind)
        except ValueError:
            rank = len(KIND_ORDER)
        return (rank, self.path or "", self.doc_id or "", self.message)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Finding(%r, %r)" % (self.kind, self.path)


class CheckResult(object):
    """Everything one ``check`` run produced."""

    __slots__ = (
        "findings",
        "mode",
        "skipped",
        "skip_reason",
        "summary",
        "doc_set",
        "impacted",
        "changed_paths",
        "code_paths",
        "doc_paths",
        "head",
    )

    def __init__(self, **kwargs):
        self.findings = kwargs.get("findings", [])
        self.mode = kwargs.get("mode", "warn")
        self.skipped = kwargs.get("skipped", False)
        self.skip_reason = kwargs.get("skip_reason")
        self.summary = kwargs.get("summary", {})
        self.doc_set = kwargs.get("doc_set")
        self.impacted = kwargs.get("impacted", [])
        self.changed_paths = kwargs.get("changed_paths", [])
        self.code_paths = kwargs.get("code_paths", [])
        self.doc_paths = kwargs.get("doc_paths", [])
        self.head = kwargs.get("head")

    @property
    def ok(self):
        return not self.findings

    def exit_code(self):
        """0 unless there are findings and the effective mode is ``error``."""
        if self.mode == "error" and self.findings:
            return 1
        return 0

    def counts(self):
        counts = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return counts


def sha_satisfies(verified_at, head_sha):
    """True when ``verified_at`` names the same commit as ``head_sha``.

    Both may be short or long, so equality is prefix equality on at least the
    shorter of the two, with a 7-character floor to avoid accidental matches.
    """
    if not verified_at or not head_sha:
        return False
    left = verified_at.strip().lower()
    right = head_sha.strip().lower()
    if not left or not right:
        return False
    shortest = min(len(left), len(right))
    if shortest < 7:
        return left == right
    return left[:shortest] == right[:shortest]


def skip_requested(repo, config, message=None, source=SOURCE_STAGED, env=None):
    """Return a reason string when this run should be skipped, else None."""
    environ = os.environ if env is None else env

    raw = environ.get("GOTDOCS_SKIP")
    if raw is not None:
        value = raw.strip()
        if value.lower() in _SKIP_ENV_TRUE:
            return "GOTDOCS_SKIP is set"
        if config.skip_token and config.skip_token in raw:
            return "GOTDOCS_SKIP contains %s" % (config.skip_token,)

    if message is None and repo is not None and source == SOURCE_STAGED:
        # Best effort only: see GitRepo.pending_commit_message. A leftover
        # COMMIT_EDITMSG from the previous commit is never treated as the
        # message being written now.
        message = repo.pending_commit_message()
    if message and config.skip_token and config.skip_token in message:
        return "commit message contains %s" % (config.skip_token,)
    return None


def run_check(
    repo_root,
    config,
    source=SOURCE_STAGED,
    base=None,
    paths=None,
    mode=None,
    message=None,
    repo=None,
    doc_set=None,
    env=None,
):
    """Execute the check and return a :class:`CheckResult`."""
    if source == SOURCE_BASE and not base:
        raise UsageError("--base needs a ref, for example: --base origin/main")
    if source == SOURCE_PATHS and not paths:
        raise UsageError("--paths needs at least one path")

    if repo is None and source != SOURCE_PATHS:
        repo = gitutil.GitRepo(repo_root)

    effective_mode = mode or config.mode_for(
        "pre_commit" if source == SOURCE_STAGED else "ci"
    )

    head = None
    if repo is not None:
        head = repo.head_sha_or_none(short=True)

    reason = skip_requested(repo, config, message=message, source=source, env=env)
    if reason is not None:
        return CheckResult(
            findings=[],
            mode=effective_mode,
            skipped=True,
            skip_reason=reason,
            head=head,
            summary=_summary([], [], [], 0, 0, head),
        )

    if effective_mode == "off":
        return CheckResult(
            findings=[],
            mode="off",
            head=head,
            summary=_summary([], [], [], 0, 0, head),
        )

    # Step 1: the change set.
    changed_paths = _change_set(repo, source, base, paths)

    # Step 2: split it.
    doc_paths = []
    code_paths = []
    ignored_paths = []
    for path in changed_paths:
        if config.is_doc_path(path):
            doc_paths.append(path)
        elif config.is_ignored(path):
            ignored_paths.append(path)
        else:
            code_paths.append(path)

    if doc_set is None:
        doc_set = index_module.scan(repo_root, config)

    changed_set = set(changed_paths)
    findings = []

    # Steps 3 and 4: impacted -> satisfied or stale.
    impacted = []
    covered_code_paths = set()
    for doc in doc_set.docs:
        matches = []
        for code_path in code_paths:
            matched = doc.covers_matches(code_path)
            if matched:
                matches.append((code_path, matched))
                covered_code_paths.add(code_path)
        if not matches:
            continue
        impacted.append((doc, matches))

        if doc.path in changed_set:
            continue  # satisfied: the author edited the doc alongside the code
        if sha_satisfies(doc.verified_at, head):
            continue  # satisfied: the author ran `gotdocs verify`

        code_path, matched = matches[0]
        extra = ""
        if len(matches) > 1:
            extra = " (and %d other file%s)" % (
                len(matches) - 1,
                "" if len(matches) == 2 else "s",
            )
        findings.append(
            Finding(
                KIND_STALE,
                doc.path,
                "%s changed and is covered by %s%s" % (code_path, matched[0], extra),
                "update %s, or run: bin/gotdocs verify %s" % (doc.path, doc.display_id),
                doc_id=doc.id,
            )
        )

    # Step 5: uncovered code, only when asked for.
    if config.require_coverage:
        for code_path in code_paths:
            if code_path in covered_code_paths:
                continue
            findings.append(
                Finding(
                    KIND_UNCOVERED,
                    code_path,
                    "%s changed and is not covered by any document" % (code_path,),
                    "add %s to the 'covers' list of a document, or create one: "
                    "bin/gotdocs new doc <id> --covers '%s'" % (code_path, code_path),
                    doc_id=None,
                )
            )

    # Step 6: doc-side findings, always reported.
    findings.extend(_lint_findings(doc_set))
    findings.extend(_duplicate_findings(doc_set))
    findings.extend(_deprecated_edit_findings(doc_set, changed_set))
    findings.extend(_index_findings(repo_root, config, doc_set, head))

    findings.sort(key=lambda finding: finding.sort_key())

    summary = _summary(
        changed_paths,
        code_paths,
        doc_paths,
        len(doc_set.docs),
        len(impacted),
        head,
        findings,
    )
    return CheckResult(
        findings=findings,
        mode=effective_mode,
        summary=summary,
        doc_set=doc_set,
        impacted=impacted,
        changed_paths=changed_paths,
        code_paths=code_paths,
        doc_paths=doc_paths,
        head=head,
    )


def _change_set(repo, source, base, paths):
    from . import globs

    if source == SOURCE_PATHS:
        return sorted(set(globs.normalize_path(path) for path in paths if path))
    if source == SOURCE_BASE:
        return repo.base_changes(base)
    if not repo.has_commits():
        # A first commit stages everything; comparing against the empty tree is
        # the documented behaviour rather than an error.
        return repo.staged_changes()
    return repo.staged_changes()


def _summary(changed_paths, code_paths, doc_paths, docs_indexed, impacted, head, findings=None):
    findings = findings or []
    counts = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return {
        "changed_paths": len(changed_paths),
        "code_paths": len(code_paths),
        "doc_paths": len(doc_paths),
        "docs_indexed": docs_indexed,
        "impacted": impacted,
        "stale": counts.get(KIND_STALE, 0),
        "uncovered": counts.get(KIND_UNCOVERED, 0),
        "findings": len(findings),
        "head": head,
    }


def _lint_findings(doc_set):
    findings = []
    for issue in doc_set.issues:
        findings.append(
            Finding(
                KIND_LINT,
                issue.path,
                "%s: %s" % (_location(issue), issue.message),
                # .gotdocs/README.md, not docs/doc-format.md: the README is
                # vendored into every target repo, the doc is not.
                "fix the frontmatter in %s; see .gotdocs/README.md" % (issue.path,),
                doc_id=None,
            )
        )
    return findings


def _location(issue):
    if issue.line is None:
        return issue.path
    return "%s:%d" % (issue.path, issue.line)


def _duplicate_findings(doc_set):
    findings = []
    for doc_id, first_path, second_path in doc_set.duplicate_ids:
        findings.append(
            Finding(
                KIND_DUPLICATE_ID,
                second_path,
                "id %r is already used by %s" % (doc_id, first_path),
                "give %s a unique 'id'" % (second_path,),
                doc_id=doc_id,
            )
        )
    return findings


def _deprecated_edit_findings(doc_set, changed_set):
    findings = []
    for doc in doc_set.docs:
        if doc.status != "deprecated":
            continue
        if doc.path not in changed_set:
            continue
        findings.append(
            Finding(
                KIND_DEPRECATED_EDIT,
                doc.path,
                "%s is marked deprecated but was edited in this change" % (doc.path,),
                "delete %s, or set 'status: current' if it is worth keeping" % (doc.path,),
                doc_id=doc.id,
            )
        )
    return findings


def _index_findings(repo_root, config, doc_set, head):
    ok, stale = index_module.index_is_current(repo_root, config, doc_set, head)
    if ok:
        return []
    return [
        Finding(
            KIND_INDEX_OUT_OF_DATE,
            path,
            "%s does not match the documents on disk" % (path,),
            "run: bin/gotdocs index   (then stage the result)",
            doc_id=None,
        )
        for path in stale
    ]


def impacted_for_paths(repo_root, config, paths, doc_set=None):
    """The read-only lookup behind ``gotdocs impacted``.

    Returns ``[{"path", "ignored", "doc_path", "docs": [...]}]`` in the order the
    paths were given, where each doc is ``{"doc_id", "path", "matched"}``.

    This mirrors step 2 of :func:`run_check` exactly: a path inside a configured
    root is a *doc path*, never a code path, so one document's ``covers`` can
    never make another document impacted. Reporting such a match here would
    promise an impact that ``check`` will never produce.
    """
    from . import globs

    if doc_set is None:
        doc_set = index_module.scan(repo_root, config)

    results = []
    for raw_path in paths:
        path = globs.normalize_path(raw_path)
        entry = {"path": path, "ignored": False, "doc_path": False, "docs": []}
        if config.is_doc_path(path):
            entry["doc_path"] = True
            results.append(entry)
            continue
        if config.is_ignored(path):
            entry["ignored"] = True
            results.append(entry)
            continue
        for doc in doc_set.docs:
            matched = doc.covers_matches(path)
            if matched:
                entry["docs"].append(
                    {"doc_id": doc.display_id, "path": doc.path, "matched": matched}
                )
        results.append(entry)
    return results


def resolve_head(repo):
    """HEAD's short sha, raising a clear error in an empty repository."""
    sha = repo.head_sha_or_none(short=True)
    if sha is None:
        raise EmptyRepoError(
            "repository has no commits yet, so there is no sha to verify against"
        )
    return sha
