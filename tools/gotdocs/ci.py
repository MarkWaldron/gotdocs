"""CI preflight: the setup that does *not* live in the workflow file.

The workflow YAML is vendored by the installer, so the file itself is never the
problem. What breaks a first run is repository state the file cannot declare:

* **Workflow permissions.** Since 2023 new repositories default to a read-only
  ``GITHUB_TOKEN``. That default is a *cap*: the ``record`` job asks for
  ``permissions: contents: write`` and still gets a read-only token, so the
  ledger commit fails at ``git push`` after everything else succeeded.
* **Branch protection.** A protected default branch that requires a pull request
  rejects the ledger push for a different reason, with a different fix.
* **The default branch is not ``main``.** The workflow triggers on
  ``push: branches: [main]``. On a ``master`` repository the ``record`` job is
  simply never invoked, and nothing anywhere says so.
* **Actions disabled** for the repository or the organisation.

Every check here is either purely local, or answered by one ``gh api`` call.
``gh`` is optional: without it the remote checks report ``unknown`` and print
the exact click path instead of failing. Nothing in this module writes anything
unless ``apply=True``.
"""

import json
import os
import re
import subprocess


GITHUB = "github"
GITLAB = "gitlab"
PROVIDERS = (GITHUB, GITLAB)

WORKFLOW_PATH = ".github/workflows/gotdocs.yml"
GITLAB_PATH = ".gitlab-ci.gotdocs.yml"

OK = "ok"
FAIL = "fail"
WARN = "warn"
UNKNOWN = "unknown"

#: Statuses that mean "this will not work as shipped".
BLOCKING = (FAIL,)


class Check(object):
    """One preflight result."""

    __slots__ = ("name", "status", "detail", "fix", "gh_command", "auto")

    def __init__(self, name, status, detail, fix="", gh_command="", auto=False):
        self.name = name
        self.status = status
        self.detail = detail
        self.fix = fix
        self.gh_command = gh_command
        #: True when ``doctor --apply`` can fix it without a human.
        self.auto = auto

    def as_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "gh_command": self.gh_command,
            "auto_fixable": self.auto,
        }


# -- helpers ---------------------------------------------------------------


def _run(argv, cwd=None):
    """Run a command, returning ``(exit_code, stdout)``; never raises."""
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except (OSError, ValueError):
        return 127, ""
    return proc.returncode, proc.stdout.strip()


def gh_available():
    """Is the ``gh`` CLI installed *and* authenticated?"""
    code, _ = _run(["gh", "--version"])
    if code != 0:
        return False
    code, _ = _run(["gh", "auth", "status"])
    return code == 0


def gh_json(root, path, method=None, fields=()):
    """Call ``gh api <path>``; return parsed JSON or ``None``."""
    argv = ["gh", "api", path]
    if method:
        argv.extend(["-X", method])
    for key, value in fields:
        argv.extend(["-f", "%s=%s" % (key, value)])
    code, out = _run(argv, cwd=root)
    if code != 0 or not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return {}


def detect_default_branch(root):
    """The remote's default branch, or ``None`` when there is no remote."""
    code, out = _run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=root
    )
    if code == 0 and out.startswith("origin/"):
        return out[len("origin/"):]
    # No origin/HEAD (a common state after a bare `git remote add`). Fall back to
    # whatever the remote advertises, then to the local branch.
    code, out = _run(["git", "remote", "show", "origin"], cwd=root)
    if code == 0:
        match = re.search(r"HEAD branch:\s*(\S+)", out)
        if match and match.group(1) != "(unknown)":
            return match.group(1)
    code, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if code == 0 and out and out != "HEAD":
        return out
    return None


def workflow_push_branches(text):
    """The branch list under ``on: push:`` in a gotdocs workflow.

    Deliberately a narrow regex rather than a YAML parser -- this module is
    stdlib-only and the shape of the file we ship is known.
    """
    match = re.search(r"^\s*push:\s*\n\s*branches:\s*\[([^\]]*)\]", text, re.M)
    if not match:
        match = re.search(r"^\s*push:\s*\n\s*branches:\s*\n((?:\s*-\s*\S+\n)+)", text, re.M)
        if not match:
            return []
        return [line.strip().lstrip("-").strip().strip("'\"")
                for line in match.group(1).splitlines() if line.strip()]
    return [part.strip().strip("'\"") for part in match.group(1).split(",") if part.strip()]


def _read(path):
    try:
        with open(path) as handle:
            return handle.read()
    except (IOError, OSError):
        return None


# -- the checks ------------------------------------------------------------


def local_checks(root):
    """Checks that need no network and no ``gh``."""
    checks = []
    workflow = os.path.join(root, WORKFLOW_PATH)
    text = _read(workflow)

    if text is None:
        checks.append(
            Check(
                "workflow-present",
                FAIL,
                "%s is missing" % (WORKFLOW_PATH,),
                "run: bin/gotdocs ci init",
                auto=True,
            )
        )
        return checks

    checks.append(Check("workflow-present", OK, "%s exists" % (WORKFLOW_PATH,)))

    if "fetch-depth: 0" in text:
        checks.append(
            Check("full-history", OK, "checkout uses fetch-depth: 0")
        )
    else:
        checks.append(
            Check(
                "full-history",
                FAIL,
                "checkout is shallow; `check --base REF...HEAD` has no merge base",
                "add `fetch-depth: 0` to every actions/checkout step",
            )
        )

    declared = workflow_push_branches(text)
    default = detect_default_branch(root)
    if not declared:
        checks.append(
            Check(
                "default-branch",
                WARN,
                "could not read the `on: push:` branch list from the workflow",
                "confirm the workflow triggers on your default branch",
            )
        )
    elif default is None:
        checks.append(
            Check(
                "default-branch",
                UNKNOWN,
                "no git remote, so the default branch is unknown "
                "(workflow triggers on: %s)" % (", ".join(declared),),
                "confirm %s is your default branch once you add a remote"
                % (declared[0],),
            )
        )
    elif default in declared:
        checks.append(
            Check("default-branch", OK, "workflow triggers on '%s'" % (default,))
        )
    else:
        checks.append(
            Check(
                "default-branch",
                FAIL,
                "default branch is '%s' but the workflow only triggers on %s -- "
                "the debt ledger job will never run"
                % (default, ", ".join(declared)),
                "run: bin/gotdocs ci init --force   (rewrites the branch list)",
                auto=True,
            )
        )

    cli_path = os.path.join(root, "bin", "gotdocs")
    if not os.path.exists(cli_path):
        checks.append(
            Check("cli-vendored", FAIL, "bin/gotdocs is missing", "re-run the installer")
        )
    elif not os.access(cli_path, os.X_OK):
        checks.append(
            Check(
                "cli-vendored",
                FAIL,
                "bin/gotdocs is not executable; CI will fail with 'Permission denied'",
                "run: chmod +x bin/gotdocs && git update-index --chmod=+x bin/gotdocs",
                auto=True,
            )
        )
    else:
        checks.append(Check("cli-vendored", OK, "bin/gotdocs is present and executable"))

    # A file mode that is right on disk but wrong in the index fails only in CI,
    # which is the most annoying way to discover it.
    code, out = _run(["git", "ls-files", "-s", "bin/gotdocs"], cwd=root)
    if code == 0 and out and not out.startswith("100755"):
        checks.append(
            Check(
                "cli-exec-bit-committed",
                FAIL,
                "bin/gotdocs is committed as %s, not 100755; it will not be "
                "executable on the runner" % (out.split()[0],),
                "run: git update-index --chmod=+x bin/gotdocs",
                auto=True,
            )
        )
    elif code == 0 and out:
        checks.append(
            Check("cli-exec-bit-committed", OK, "bin/gotdocs is committed executable")
        )

    return checks


def remote_checks(root, have_gh=None):
    """Checks answered by the GitHub API. Degrade to ``unknown`` without ``gh``."""
    if have_gh is None:
        have_gh = gh_available()

    click_path = (
        "Settings -> Actions -> General -> Workflow permissions -> "
        "'Read and write permissions' -> Save"
    )
    perms_cmd = (
        "gh api -X PUT repos/{owner}/{repo}/actions/permissions/workflow "
        "-F default_workflow_permissions=write"
    )

    if not have_gh:
        return [
            Check(
                "workflow-token-permissions",
                UNKNOWN,
                "cannot verify without an authenticated `gh` "
                "(install: brew install gh && gh auth login)",
                click_path,
                perms_cmd,
            ),
            Check(
                "branch-protection",
                UNKNOWN,
                "cannot verify without an authenticated `gh`",
                "if the default branch requires pull requests, the ledger push "
                "will be rejected -- see the fix options in `ci doctor --help`",
            ),
        ]

    checks = []
    perms = gh_json(root, "repos/{owner}/{repo}/actions/permissions/workflow")
    if perms is None:
        checks.append(
            Check(
                "workflow-token-permissions",
                UNKNOWN,
                "gh could not read the repository's workflow permissions "
                "(no remote, no access, or the repo does not exist yet)",
                click_path,
                perms_cmd,
            )
        )
    elif perms.get("default_workflow_permissions") == "write":
        checks.append(
            Check(
                "workflow-token-permissions",
                OK,
                "GITHUB_TOKEN defaults to read and write",
            )
        )
    else:
        checks.append(
            Check(
                "workflow-token-permissions",
                FAIL,
                "GITHUB_TOKEN is read-only for this repository, which caps the "
                "record job's `contents: write` -- the ledger commit will fail "
                "at git push",
                click_path,
                perms_cmd,
                auto=True,
            )
        )

    default = detect_default_branch(root) or "main"
    protection = gh_json(
        root, "repos/{owner}/{repo}/branches/%s/protection" % (default,)
    )
    if protection is None:
        # 404 is the overwhelmingly common case and means "not protected".
        checks.append(
            Check(
                "branch-protection",
                OK,
                "'%s' is not protected, so the ledger push succeeds" % (default,),
            )
        )
    elif protection.get("required_pull_request_reviews"):
        checks.append(
            Check(
                "branch-protection",
                FAIL,
                "'%s' requires pull request reviews; the record job's direct "
                "push will be rejected" % (default,),
                "pick one: (a) set enforce.ci to 'error' and drop the record "
                "job, (b) allow the actions bot to bypass the rule, or "
                "(c) have the job open a pull request instead of pushing",
            )
        )
    else:
        checks.append(
            Check(
                "branch-protection",
                WARN,
                "'%s' has branch protection; confirm it permits the actions bot "
                "to push" % (default,),
                "Settings -> Branches -> the rule -> allow GitHub Actions to bypass",
            )
        )

    return checks


def run_doctor(root, apply_fixes=False):
    """Run every check. Returns ``(checks, applied)``."""
    have_gh = gh_available()
    checks = local_checks(root) + remote_checks(root, have_gh=have_gh)
    applied = []
    if not apply_fixes:
        return checks, applied

    for check in checks:
        if check.status not in BLOCKING or not check.auto:
            continue
        if check.name == "workflow-token-permissions" and have_gh:
            code, _ = _run(
                [
                    "gh", "api", "-X", "PUT",
                    "repos/{owner}/{repo}/actions/permissions/workflow",
                    "-F", "default_workflow_permissions=write",
                ],
                cwd=root,
            )
            if code == 0:
                check.status = OK
                check.detail = "GITHUB_TOKEN set to read and write"
                applied.append(check.name)
        elif check.name in ("cli-vendored", "cli-exec-bit-committed"):
            path = os.path.join(root, "bin", "gotdocs")
            if os.path.exists(path):
                os.chmod(path, os.stat(path).st_mode | 0o111)
                _run(["git", "update-index", "--chmod=+x", "bin/gotdocs"], cwd=root)
                check.status = OK
                check.detail = "bin/gotdocs made executable, in the tree and the index"
                applied.append(check.name)
        elif check.name in ("default-branch", "workflow-present"):
            written = init_workflow(root, GITHUB, force=True)
            if written:
                check.status = OK
                check.detail = "workflow rewritten for the current default branch"
                applied.append(check.name)

    return checks, applied


# -- generation ------------------------------------------------------------


GITLAB_TEMPLATE = """\
# gotdocs -- documentation freshness. Include from .gitlab-ci.yml:
#   include: {local: '%(path)s'}
#
# The three gates, in the order they matter. `check` compares against the
# *target* branch: comparing a branch with itself is an empty diff and a gate
# that can never fail.
gotdocs:
  stage: test
  image: python:3.11-slim
  variables:
    GIT_DEPTH: 0          # REF...HEAD needs a merge base; a shallow clone has none
  before_script:
    - git config --global --add safe.directory "$CI_PROJECT_DIR"
  script:
    - bin/gotdocs lint
    - bin/gotdocs check --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --mode %(mode)s
    - bin/gotdocs index
    - git diff --exit-code -- .gotdocs/index.json .gotdocs/INDEX.md
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  allow_failure: %(allow_failure)s
"""


def init_workflow(root, provider, force=False, mode="warn"):
    """Write the CI definition for ``provider``. Returns the path, or ``None``.

    For GitHub this refreshes the vendored workflow's ``on: push: branches:``
    list to the repository's real default branch, which is the one edit every
    non-``main`` repository needs and nobody remembers to make.
    """
    if provider == GITHUB:
        path = os.path.join(root, WORKFLOW_PATH)
        text = _read(path)
        if text is None:
            return None
        default = detect_default_branch(root)
        if not default:
            return None
        declared = workflow_push_branches(text)
        if declared == [default] and not force:
            return None
        updated = re.sub(
            r"(^\s*push:\s*\n\s*branches:\s*)\[[^\]]*\]",
            lambda m: "%s[%s]" % (m.group(1), default),
            text,
            count=1,
            flags=re.M,
        )
        if updated == text:
            return None
        with open(path, "w") as handle:
            handle.write(updated)
        return WORKFLOW_PATH

    if provider == GITLAB:
        path = os.path.join(root, GITLAB_PATH)
        if os.path.exists(path) and not force:
            return None
        payload = GITLAB_TEMPLATE % {
            "path": GITLAB_PATH,
            "mode": mode,
            "allow_failure": "true" if mode != "error" else "false",
        }
        with open(path, "w") as handle:
            handle.write(payload)
        return GITLAB_PATH

    raise ValueError("unknown provider: %r" % (provider,))


def summarize(checks):
    """``{status: count}`` plus an overall boolean."""
    counts = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return {
        "counts": counts,
        "ok": not any(check.status in BLOCKING for check in checks),
    }
