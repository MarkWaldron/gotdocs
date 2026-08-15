"""Renders findings as grouped human text or as the ``--json`` contract.

The JSON shape is the agent interface and is stable (docs/cli-reference.md#the---json-contract):

.. code-block:: json

    {"ok": false, "mode": "warn", "findings": [...], "summary": {...}}

Errors reuse the same envelope with an added ``error`` object, so a consumer
never has to parse two shapes.
"""

import json

from .check import KIND_ORDER
from .decisions import lead_claim

__all__ = [
    "Palette",
    "envelope",
    "error_envelope",
    "dumps",
    "render_check_text",
    "render_check_json",
    "render_impacted_text",
    "render_impacted_json",
    "render_lint_text",
    "render_lint_json",
    "render_index_text",
    "render_index_json",
    "render_status_text",
    "render_why_json",
    "render_why_path_text",
    "render_targets_text",
    "render_export_json",
    "render_export_text",
    "render_debt_record_text",
    "render_debt_list_text",
    "render_debt_resolve_text",
    "render_debt_stats_text",
]


class Palette(object):
    """ANSI colors, or a no-op when color is disabled."""

    def __init__(self, enabled):
        self.enabled = bool(enabled)

    def _wrap(self, code, text):
        if not self.enabled:
            return text
        return "\033[%sm%s\033[0m" % (code, text)

    def bold(self, text):
        return self._wrap("1", text)

    def red(self, text):
        return self._wrap("31", text)

    def yellow(self, text):
        return self._wrap("33", text)

    def green(self, text):
        return self._wrap("32", text)

    def dim(self, text):
        return self._wrap("2", text)


def dumps(payload):
    """Serialize a JSON payload for stdout, with a trailing newline."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def envelope(ok, mode, findings, summary):
    return {
        "ok": bool(ok),
        "mode": mode,
        "findings": list(findings),
        "summary": dict(summary),
    }


def error_envelope(code, message, mode="error"):
    payload = envelope(False, mode, [], {})
    payload["error"] = {"code": code, "message": message}
    return payload


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def render_check_json(result):
    payload = envelope(result.ok, result.mode, [f.as_dict() for f in result.findings], result.summary)
    if result.skipped:
        payload["skipped"] = True
        payload["skip_reason"] = result.skip_reason
    return dumps(payload)


def render_check_text(result, palette=None, quiet=False):
    """Group findings by kind; every line carries a remediation."""
    palette = palette or Palette(False)
    lines = []

    if result.skipped:
        if quiet:
            return ""
        return "gotdocs: skipped (%s)\n" % (result.skip_reason,)

    if result.mode == "off":
        if quiet:
            return ""
        return "gotdocs: enforcement is off, nothing checked\n"

    count = len(result.findings)
    if count == 0:
        if quiet:
            return ""
        return "gotdocs: %s (mode: %s)\n" % (
            palette.green("no findings"),
            result.mode,
        )

    headline = "gotdocs: %d finding%s (mode: %s)" % (
        count,
        "" if count == 1 else "s",
        result.mode,
    )
    lines.append(palette.red(headline) if result.mode == "error" else palette.yellow(headline))
    lines.append("")

    grouped = {}
    for finding in result.findings:
        grouped.setdefault(finding.kind, []).append(finding)

    order = [kind for kind in KIND_ORDER if kind in grouped]
    order.extend(sorted(kind for kind in grouped if kind not in KIND_ORDER))

    for kind in order:
        items = grouped[kind]
        lines.append("%s (%d)" % (palette.bold(kind), len(items)))
        for finding in items:
            label = finding.path or ""
            if finding.doc_id:
                label = "%s  [%s]" % (label, finding.doc_id)
            lines.append("  %s" % (label,))
            lines.append("    %s" % (finding.message,))
            lines.append("    %s %s" % (palette.dim("->"), finding.remediation))
        lines.append("")

    lines.append("Or ask Claude: /gotdocs-update")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# impacted
# ---------------------------------------------------------------------------


def render_impacted_json(entries):
    return dumps({"ok": True, "paths": entries})


def render_impacted_text(entries, palette=None):
    palette = palette or Palette(False)
    lines = []
    id_width = 0
    path_width = 0
    for entry in entries:
        for doc in entry["docs"]:
            id_width = max(id_width, len(doc["doc_id"]))
            path_width = max(path_width, len(doc["path"]))

    for entry in entries:
        if entry["ignored"]:
            suffix = "  (ignored)"
        elif entry.get("doc_path"):
            suffix = "  (doc)"
        else:
            suffix = ""
        lines.append("%s%s" % (entry["path"], palette.dim(suffix)))
        if not entry["docs"]:
            if entry.get("doc_path"):
                lines.append(
                    "  %s"
                    % (
                        palette.dim(
                            "inside a doc root; doc paths are never code paths"
                        ),
                    )
                )
            elif not entry["ignored"]:
                lines.append("  %s" % (palette.dim("no documents cover this path"),))
            continue
        for doc in entry["docs"]:
            lines.append(
                "  %s  %s  (%s)"
                % (
                    doc["doc_id"].ljust(id_width),
                    doc["path"].ljust(path_width),
                    ", ".join(doc["matched"]),
                )
            )
    return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def render_lint_json(findings, ok, warnings=None):
    """The lint envelope.

    ``findings`` keeps its meaning exactly: things that fail the command.
    Warnings (``lint --portability`` without ``--strict``) are a separate list
    so a consumer that only reads ``findings`` and ``ok`` behaves as before.
    """
    payload = envelope(ok, "error", [f.as_dict() for f in findings], {"findings": len(findings)})
    warnings = list(warnings or [])
    payload["warnings"] = [f.as_dict() for f in warnings]
    payload["summary"]["warnings"] = len(warnings)
    return dumps(payload)


def render_lint_text(findings, doc_count, palette=None, quiet=False, warnings=None):
    palette = palette or Palette(False)
    warnings = list(warnings or [])
    lines = []
    if not findings:
        if not quiet:
            lines.append(
                "gotdocs: %s in %d document%s"
                % (
                    palette.green("no lint errors"),
                    doc_count,
                    "" if doc_count == 1 else "s",
                )
            )
    else:
        lines.append(
            palette.red(
                "gotdocs: %d lint error%s in %d document%s"
                % (
                    len(findings),
                    "" if len(findings) == 1 else "s",
                    doc_count,
                    "" if doc_count == 1 else "s",
                )
            )
        )
        lines.append("")
        for finding in findings:
            lines.append("  %s" % (finding.message,))
            lines.append("    %s %s" % (palette.dim("->"), finding.remediation))

    if warnings:
        if lines:
            lines.append("")
        lines.append(
            palette.yellow(
                "gotdocs: %d portability warning%s (not blocking; re-run with --strict to fail on them)"
                % (len(warnings), "" if len(warnings) == 1 else "s")
            )
        )
        lines.append("")
        for finding in warnings:
            lines.append("  %s" % (finding.message,))
            lines.append("    %s %s" % (palette.dim("->"), finding.remediation))

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# index / status
# ---------------------------------------------------------------------------


def render_index_json(doc_count, changed):
    return dumps({"ok": True, "doc_count": doc_count, "changed": list(changed)})


def render_index_text(doc_count, changed, palette=None, quiet=False):
    palette = palette or Palette(False)
    if quiet:
        return ""
    if not changed:
        return "gotdocs: %d document%s indexed, no changes\n" % (
            doc_count,
            "" if doc_count == 1 else "s",
        )
    return "gotdocs: %d document%s indexed, rewrote %s\n" % (
        doc_count,
        "" if doc_count == 1 else "s",
        ", ".join(changed),
    )


def render_status_text(state, palette=None):
    """One screen of state, for humans and for agents entering a new repo."""
    palette = palette or Palette(False)
    rows = [
        ("gotdocs %s" % (state["version"],), "repo %s  head %s" % (state["repo"], state["head"] or "(no commits)")),
    ]
    lines = ["%s  %s" % (palette.bold(rows[0][0]), rows[0][1])]

    def row(label, value):
        lines.append("%-9s %s" % (label, value))

    row("config", state["config"])
    row("roots", ", ".join(state["roots"]) or "(none)")
    counts = state["status_counts"]
    row(
        "docs",
        "%d (%d current, %d draft, %d deprecated)"
        % (
            state["doc_count"],
            counts.get("current", 0),
            counts.get("draft", 0),
            counts.get("deprecated", 0),
        ),
    )
    row("index", state["index"])
    row(
        "enforce",
        "pre_commit=%s  pre_push=%s  ci=%s  require_coverage=%s"
        % (
            state["enforce"]["pre_commit"],
            state["enforce"].get("pre_push", "warn"),
            state["enforce"]["ci"],
            "true" if state["require_coverage"] else "false",
        ),
    )
    row("hook", state["hook"])
    if state.get("lint_errors"):
        row("lint", "%d error(s) — run: bin/gotdocs lint" % (state["lint_errors"],))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# why
# ---------------------------------------------------------------------------


def _ordered_entry(entry):
    """Turn a Decision/Match ``as_entry()`` into an order-preserving dict."""
    order = entry.pop("_order", None) or sorted(entry)
    result = {}
    for key in order:
        if key in entry:
            result[key] = entry[key]
    for key in entry:
        if key not in result:
            result[key] = entry[key]
    return result


def render_why_json(matches, query, path, searched, limit=None):
    shown = matches if limit is None else matches[: max(0, limit)]
    return dumps(
        {
            "ok": True,
            "query": query,
            "path": path,
            "searched": searched,
            "match_count": len(matches),
            "matches": [_ordered_entry(match.as_entry()) for match in shown],
        }
    )


def render_why_path_text(matches, path, searched, limit=None, full=False, palette=None):
    """``why --path`` with no query: the decisions that govern that file."""
    palette = palette or Palette(False)
    shown = matches if limit is None else matches[: max(0, limit)]
    if not matches:
        return (
            "no decision record covers %s (of %d searched).\n"
            "\n"
            "Nothing was written down about this file. Treat its behaviour as\n"
            "unintended until proven otherwise.\n" % (path, searched)
        )

    lines = [
        "%d decision%s cover%s %s (of %d searched):"
        % (
            len(matches),
            "" if len(matches) == 1 else "s",
            "s" if len(matches) == 1 else "",
            path,
            searched,
        )
    ]
    for position, match in enumerate(shown, 1):
        decision = match.decision
        lines.append("")
        lines.append(
            "[%d] %s  (%s)  %s"
            % (position, decision.display_id, decision.status or "status unknown", decision.path)
        )
        if decision.title:
            lines.append("    %s" % (decision.title,))
        lines.append(
            "    expected: %s"
            % (_clip(lead_claim(decision.expected, full), full)
               or "(not recorded)",)
        )
        lines.append(
            "    bug if:   %s"
            % (_clip(lead_claim(decision.not_this, full), full)
               or "(not recorded)",)
        )
    hidden = len(matches) - len(shown)
    if hidden > 0:
        lines.append("")
        lines.append("%d further record%s not shown." % (hidden, "" if hidden == 1 else "s"))
    return "\n".join(lines) + "\n"


def _clip(text, full=False, width=80):
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if full or len(collapsed) <= width:
        return collapsed
    return collapsed[: width - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def render_targets_text(targets, palette=None):
    palette = palette or Palette(False)
    lines = []
    for target in targets:
        lines.append("%s  (links: %s)" % (palette.bold(target.name), target.link_style))
        keys = target.as_dict()["keys"]
        if keys:
            lines.append(
                "  keys:     %s" % (", ".join("%s -> %s" % (k, keys[k]) for k in sorted(keys)),)
            )
        lines.append("  stripped: %s" % (", ".join(target.as_dict()["stripped"]),))
        if target.notes:
            lines.append("  %s" % (palette.dim(target.notes),))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_export_json(result, dry_run=False):
    from .export import MANIFEST_NAME

    payload = {"ok": True, "dry_run": bool(dry_run)}
    payload.update(result.summary())
    payload["written"] = list(result.written)
    payload["skipped_docs"] = list(result.skipped)
    payload["manifest"] = MANIFEST_NAME
    return dumps(payload)


def render_export_text(result, dry_run=False, palette=None, quiet=False):
    palette = palette or Palette(False)
    if quiet:
        return ""
    summary = result.summary()
    verb = "would export" if dry_run else "exported"
    lines = [
        "gotdocs: %s %d document%s for %s -> %s"
        % (
            verb,
            summary["documents"],
            "" if summary["documents"] == 1 else "s",
            palette.bold(summary["target"]),
            summary["out_dir"],
        )
    ]
    if summary["assets"]:
        lines.append("         %d asset(s) copied" % (summary["assets"],))
    if summary["skipped"]:
        lines.append(
            "         %d draft(s) skipped (use --include-drafts to publish them)"
            % (summary["skipped"],)
        )
    if not dry_run:
        if result.written:
            lines.append("         %d file(s) changed on disk" % (len(result.written),))
        else:
            lines.append("         no files changed")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# debt
# ---------------------------------------------------------------------------


def _debt_headline(summary, palette):
    return "%s open, %s resolved, %d occurrence(s) recorded" % (
        palette.bold(str(summary["open"])),
        summary["resolved"],
        summary["open_occurrences"],
    )


def render_debt_record_text(payload, palette=None, quiet=False):
    palette = palette or Palette(False)
    if quiet:
        return ""
    summary = payload["summary"]
    lines = []
    counts = (
        len(payload["added"]),
        len(payload["updated"]),
        len(payload["reopened"]),
        len(payload["resolved"]),
    )
    lines.append(
        "gotdocs: debt %s  (+%d new, ~%d seen again, ^%d reopened, -%d resolved)"
        % ("preview" if payload["dry_run"] else "recorded", counts[0], counts[1], counts[2], counts[3])
    )
    lines.append("         ledger %s @ %s %s" % (payload["ledger"], payload["date"], payload["sha"] or "(no commit)"))
    lines.append("         %s" % (_debt_headline(summary, palette),))
    if not payload["dry_run"] and not payload["written"]:
        lines.append("         ledger unchanged, nothing to commit")
    for error in payload.get("ledger_errors") or []:
        lines.append(
            palette.yellow("         line %d skipped: %s" % (error["line"], error["message"]))
        )
    return "\n".join(lines) + "\n"


def render_debt_list_text(entries, total, summary, palette=None):
    palette = palette or Palette(False)
    if not entries:
        return "gotdocs: no matching debt entries (%s)\n" % (_debt_headline(summary, palette),)
    lines = [
        "gotdocs: %d of %d entr%s  (%s)"
        % (len(entries), total, "y" if total == 1 else "ies", _debt_headline(summary, palette)),
        "",
    ]
    for entry in entries:
        marker = "open" if entry.is_open else "done"
        lines.append(
            "  %s  %s  %s  x%d  first %s  last %s"
            % (
                entry.entry_id,
                marker,
                entry.kind,
                entry.occurrences,
                entry.first_seen_date,
                entry.last_seen_date,
            )
        )
        lines.append("    %s%s" % (entry.path or "-", "  [%s]" % entry.doc_id if entry.doc_id else ""))
        if entry.message:
            lines.append("    %s" % (entry.message,))
        if entry.remediation:
            lines.append("    %s %s" % (palette.dim("->"), entry.remediation))
    return "\n".join(lines) + "\n"


def render_debt_resolve_text(payload, palette=None):
    palette = palette or Palette(False)
    lines = []
    if payload["resolved"]:
        lines.append(
            "gotdocs: resolved %d debt entr%s: %s"
            % (
                len(payload["resolved"]),
                "y" if len(payload["resolved"]) == 1 else "ies",
                ", ".join(payload["resolved"]),
            )
        )
    if payload["unmatched"]:
        lines.append(
            palette.red(
                "gotdocs: no debt entry matches %s" % (", ".join(payload["unmatched"]),)
            )
        )
        lines.append("         run: bin/gotdocs debt list --all")
    if not payload["written"] and payload["resolved"]:
        lines.append("         ledger unchanged (already resolved)")
    lines.append("         %s" % (_debt_headline(payload["summary"], palette),))
    return "\n".join(lines) + "\n"


def render_debt_stats_text(summary, ledger, palette=None):
    palette = palette or Palette(False)
    lines = ["gotdocs: %s  (%s)" % (_debt_headline(summary, palette), ledger)]
    by_kind = summary["open_by_kind"]
    if by_kind:
        lines.append("")
        lines.append("open by kind")
        for kind in [k for k in KIND_ORDER if k in by_kind] + sorted(
            k for k in by_kind if k not in KIND_ORDER
        ):
            lines.append("  %-20s %d" % (kind, by_kind[kind]))
    by_doc = summary["open_by_doc"]
    if by_doc:
        top = sorted(by_doc.items(), key=lambda item: (-item[1], item[0]))[:5]
        lines.append("")
        lines.append("worst offenders")
        for name, count in top:
            lines.append("  %-40s %d" % (name, count))
    return "\n".join(lines) + "\n"
