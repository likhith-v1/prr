"""prr CLI.

Commands:
  prr review <file>            review a single local file
  prr scan <path>              scan a file or directory
  prr review --pr owner/repo#n post an inline review on a GitHub PR
  prr eval                     run seeded regression eval cases
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core.config import ConfigError, PrrConfig, load_config
from core.context import build_context, findings_for_chunk
from core.detect_static import ToolName, run_static_tools
from core.diff import PatchInfo, parse_patch
from core.eval import EvalError, EvalModelError, EvalReport, run_eval
from core.filter import filter_findings
from core.github_out import (
    GithubClient,
    GithubError,
    build_comment,
    build_summary,
    parse_pr_ref,
)
from core.ingest import chunk_file
from core.model import ModelBackendError, review
from core.schema import Finding, MOOD

console = Console()

_PURR = """\
  /\\_____/\\
 (  ≧ ω ≦ )   prr is purring — nothing to flag
  > ♡♡♡♡♡ <
"""

_SWAT = """\
  /\\_____/\\
 (  ⩺ × ⩻ )   prr is not happy
  > !! !! <
"""


# ── severity colours ──────────────────────────────────────────────────────────
_COLOUR = {"error": "red", "warning": "yellow", "info": "cyan"}
_BORDER = {"error": "red", "warning": "yellow", "info": "blue"}


def _render_finding(f: Finding) -> None:
    mood = MOOD[f.severity]
    colour = _COLOUR[f.severity]
    header = Text()
    header.append(f"{mood}  ", style=colour + " bold")
    header.append(f"line {f.line}", style="bold")
    header.append(f"  [{f.category}]", style="dim")
    if f.confidence < 1.0:
        header.append(f"  confidence {f.confidence:.0%}", style="dim")

    body = Text(f.comment)
    if f.suggestion:
        body.append("\n\nsuggestion:\n", style="dim")
        body.append(f.suggestion, style="green")

    panel = Panel(
        Text.assemble(header, "\n\n", body),
        border_style=_BORDER[f.severity],
        expand=False,
        padding=(0, 1),
    )
    console.print(panel)


def _load_config_for_args(args: argparse.Namespace) -> PrrConfig | None:
    try:
        return load_config(getattr(args, "config", None))
    except ConfigError as exc:
        console.print("[red]Config error.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return None


def _validate_python_file(path: Path) -> bool:
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        return False
    if not path.is_file():
        console.print(f"[red]Not a file:[/red] {path}")
        return False
    if path.suffix != ".py":
        console.print(f"[red]Not a Python file:[/red] {path}")
        return False
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        console.print(f"[red]Could not decode as UTF-8:[/red] {path}")
        return False
    except OSError as exc:
        console.print(f"[red]Could not read file:[/red] {path}")
        console.print(f"[dim]{exc}[/dim]")
        return False
    return True


def _review_file(
    path: Path,
    config: PrrConfig,
    static_findings: list[Finding],
    root: Path,
    only_lines: set[int] | None = None,
) -> tuple[list[Finding], bool, bool]:
    """Review one file; returns (findings, ok, had_chunks).

    When *only_lines* is given (PR mode), chunks that do not overlap any of
    those line numbers are skipped.
    """
    try:
        chunks = chunk_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        console.print(f"[red]Could not chunk file:[/red] {path}")
        console.print(f"[dim]{exc}[/dim]")
        return [], False, False

    if not chunks:
        return [], True, False

    all_findings: list[Finding] = []
    for chunk in chunks:
        if only_lines is not None and only_lines.isdisjoint(
            range(chunk.start_line, chunk.end_line + 1)
        ):
            continue
        console.print(
            f"  [dim]→ {chunk.kind} [bold]{chunk.name}[/bold]"
            f"  (lines {chunk.start_line}–{chunk.end_line})[/dim]"
        )
        prior = findings_for_chunk(chunk, static_findings, root=root)
        try:
            found = review(
                code=chunk.code,
                path=str(path),
                start_line=chunk.start_line,
                context=build_context(chunk),
                findings=prior,
                model=config.model,
                ollama_host=config.ollama_host,
            )
        except ModelBackendError as exc:
            console.print("[red]Model backend failed.[/red]")
            console.print(f"[dim]{exc}[/dim]")
            console.print(
                "[dim]Start Ollama and make sure the model is available: "
                f"ollama pull {config.model}[/dim]"
            )
            if config.ollama_host:
                console.print(
                    f"[dim]Using ollama_host {config.ollama_host} — confirm it is "
                    "reachable (e.g. from WSL to a Windows/remote GPU host).[/dim]"
                )
            return [], False, True
        all_findings.extend(found)
    return all_findings, True, True


def _render_findings(findings: list[Finding], target: Path) -> int:
    console.print()

    if not findings:
        console.print(_PURR, style="green")
        return 0

    console.print(_SWAT, style="red")
    console.print(
        f"[bold]{len(findings)} finding(s)[/bold] in [cyan]{target}[/cyan]\n"
    )
    for finding in findings:
        _render_finding(finding)
        console.print()

    return 1 if any(f.severity == "error" for f in findings) else 0


def cmd_review(args: argparse.Namespace) -> int:
    if getattr(args, "pr", None):
        if getattr(args, "file", None):
            console.print("[red]Pass either a file or --pr, not both.[/red]")
            return 1
        return cmd_review_pr(args)
    if not getattr(args, "file", None):
        console.print("[red]Pass a Python file to review, or --pr owner/repo#n.[/red]")
        return 1

    config = _load_config_for_args(args)
    if config is None:
        return 1

    path = Path(args.file)
    if not _validate_python_file(path):
        return 1

    console.print(f"\n[bold]prr[/bold] reviewing [cyan]{path}[/cyan] …\n")

    static_findings = _run_static_tools([path], root=Path.cwd())
    llm_findings, ok, had_chunks = _review_file(path, config, static_findings, root=Path.cwd())
    if not ok:
        return 2

    all_findings = filter_findings(
        [*static_findings, *llm_findings],
        config=config,
        root=Path.cwd(),
    )
    if not all_findings and not had_chunks:
        console.print("[dim]File is empty.[/dim]")
        return 0

    return _render_findings(all_findings, path)


def _matches_ignore(rel: str, config: PrrConfig) -> bool:
    # Patterns like ".venv/**" must also catch nested trees ("sub/.venv/...").
    return any(
        fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, f"**/{pattern}")
        for pattern in config.ignore_paths
    )


def _is_ignored(path: Path, root: Path, config: PrrConfig) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return _matches_ignore(rel, config)


def _collect_python_files(target: Path, config: PrrConfig) -> tuple[Path, list[Path]] | None:
    if not target.exists():
        console.print(f"[red]Path not found:[/red] {target}")
        return None
    if target.is_file():
        if not _validate_python_file(target):
            return None
        return target.parent, [target]
    if not target.is_dir():
        console.print(f"[red]Not a file or directory:[/red] {target}")
        return None

    root = target
    files = [
        path
        for path in sorted(root.rglob("*.py"))
        if path.is_file() and not _is_ignored(path, root, config)
    ]
    return root, files


def _render_scan_findings(findings: list[Finding], target: Path) -> int:
    console.print()
    if not findings:
        console.print(_PURR, style="green")
        return 0

    by_file: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_file[finding.path].append(finding)

    console.print(_SWAT, style="red")
    console.print(f"[bold]{len(findings)} finding(s)[/bold] under [cyan]{target}[/cyan]\n")
    for file_path in sorted(by_file):
        console.print(f"[bold cyan]{file_path}[/bold cyan]")
        for finding in by_file[file_path]:
            _render_finding(finding)
            console.print()

    return 1 if any(f.severity == "error" for f in findings) else 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = _load_config_for_args(args)
    if config is None:
        return 1

    target = Path(args.path)
    collected = _collect_python_files(target, config)
    if collected is None:
        return 1
    root, files = collected
    if not files:
        console.print(f"[dim]No Python files found under {target}.[/dim]")
        return 0

    console.print(f"\n[bold]prr[/bold] scanning [cyan]{target}[/cyan] …\n")
    static_findings = _run_static_tools(files, root=root)

    llm_findings: list[Finding] = []
    for path in files:
        console.print(f"[dim]file {path}[/dim]")
        found, ok, _ = _review_file(path, config, static_findings, root=root)
        if not ok:
            return 2
        llm_findings.extend(found)

    findings = filter_findings(
        [*static_findings, *llm_findings],
        config=config,
        root=root,
    )
    return _render_scan_findings(findings, target)


# ── eval mode ─────────────────────────────────────────────────────────────────

def _render_eval_report(report: EvalReport) -> int:
    console.print()
    console.print(
        "[bold]prr eval[/bold] "
        f"caught [green]{report.caught_count}/{report.expected_count}[/green] expected issue(s), "
        f"missed [red]{report.missed_count}[/red], "
        f"false positives [red]{report.false_positive_count}[/red]."
    )

    for result in report.cases:
        status = "[green]pass[/green]" if not result.missed and not result.false_positives else "[red]fail[/red]"
        console.print(
            f"\n{status} [bold cyan]{result.case.id}[/bold cyan] "
            f"({len(result.caught)}/{len(result.case.expected)} caught)"
        )
        for missed in result.missed:
            expected = missed.expected
            console.print(
                "[red]missed[/red] "
                f"line {expected.line} [{expected.category}] "
                f"min severity {expected.min_severity}"
            )
        for false_positive in result.false_positives:
            finding = false_positive.finding
            console.print(
                "[yellow]false positive[/yellow] "
                f"line {finding.line} [{finding.category}/{finding.severity}] "
                f"{finding.comment}"
            )

    return 0 if report.ok else 1


def cmd_eval(args: argparse.Namespace) -> int:
    config = _load_config_for_args(args)
    if config is None:
        return 2

    try:
        report = run_eval(
            config=config,
            cases_path=getattr(args, "cases", None),
        )
    except EvalModelError as exc:
        console.print("[red]Eval failed.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        console.print(
            "[dim]Start Ollama and make sure the model is available: "
            f"ollama pull {config.model}[/dim]"
        )
        if config.ollama_host:
            console.print(
                f"[dim]Using ollama_host {config.ollama_host} — confirm it is "
                "reachable (e.g. from WSL to a Windows/remote GPU host).[/dim]"
            )
        return 2
    except EvalError as exc:
        console.print("[red]Eval failed.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 2

    return _render_eval_report(report)


# ── PR mode ───────────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"error": 2, "warning": 1, "info": 0}
_PR_STATIC_TOOLS: tuple[ToolName, ...] = ("ruff", "bandit")


@dataclass(frozen=True)
class PrTarget:
    path: str
    patch_info: PatchInfo


@dataclass(frozen=True)
class SkippedPrFile:
    path: str
    reason: str


def _github_client() -> GithubClient:
    """Seam for tests: patched to inject a fake client."""
    return GithubClient()


def _pr_head_sha(pr_data: dict[str, object]) -> str:
    head = pr_data["head"]
    if not isinstance(head, dict):
        raise TypeError("PR response head must be an object")
    sha = head["sha"]
    if not isinstance(sha, str) or not sha:
        raise TypeError("PR response head.sha must be a non-empty string")
    return sha


def _expected_pr_head_sha(args: argparse.Namespace) -> str | None:
    value = getattr(args, "pr_head_sha", None)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _head_changed(baseline_sha: str, current_sha: str, pr_ref: str) -> bool:
    if current_sha == baseline_sha:
        return False
    console.print(
        "[yellow]Skipping review:[/yellow] "
        f"{pr_ref} moved from {baseline_sha} to {current_sha}."
    )
    return True


def _refresh_pr_head_sha(
    client: GithubClient,
    owner: str,
    repo: str,
    number: int,
    baseline_sha: str,
    pr_ref: str,
) -> str | None:
    """Return the current PR head SHA when it still matches baseline_sha."""
    current_sha = _pr_head_sha(client.get_pr(owner, repo, number))
    if _head_changed(baseline_sha, current_sha, pr_ref):
        return None
    return current_sha


def _pr_review_targets(
    pr_files: list[dict[str, object]],
    config: PrrConfig,
) -> tuple[list[PrTarget], list[SkippedPrFile]]:
    """Select reviewable PR files and track skipped Python files."""
    targets: list[PrTarget] = []
    skipped: list[SkippedPrFile] = []
    for item in pr_files:
        filename = str(item.get("filename") or "")
        patch = item.get("patch")
        if not filename.endswith(".py"):
            continue
        if item.get("status") == "removed":
            skipped.append(SkippedPrFile(filename, "removed file"))
            continue
        if _matches_ignore(filename, config):
            skipped.append(SkippedPrFile(filename, "ignored by config"))
            continue
        if not isinstance(patch, str) or not patch:
            skipped.append(SkippedPrFile(filename, "no usable GitHub patch"))
            continue
        patch_info = parse_patch(patch)
        if not patch_info.added_lines:
            skipped.append(SkippedPrFile(filename, "no added Python lines"))
            continue
        targets.append(PrTarget(filename, patch_info))
    return targets, skipped


def _cap_pr_findings(findings: list[Finding], config: PrrConfig) -> tuple[list[Finding], int]:
    """Keep the top findings by severity under the per-PR cap."""
    prioritized = sorted(
        findings,
        key=lambda f: (-_SEVERITY_RANK[f.severity], f.path, f.line),
    )
    kept = prioritized[: config.max_comments_per_pr]
    kept.sort(key=lambda f: (f.path, f.line))
    return kept, len(prioritized) - len(kept)


def _print_static_tool_warnings(warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        console.print(f"[yellow]Static analysis skipped:[/yellow] {warning}")


def _run_static_tools(
    paths: list[Path],
    root: Path,
    tools: tuple[ToolName, ...] | None = None,
) -> list[Finding]:
    if tools is None:
        result = run_static_tools(paths, root=root)
    else:
        result = run_static_tools(paths, root=root, tools=tools)
    _print_static_tool_warnings(result.warnings)
    return result.findings


def _pr_exit_code(has_errors: bool, args: argparse.Namespace) -> int:
    if getattr(args, "no_fail_on_findings", False):
        return 0
    return 1 if has_errors else 0


def _pr_review_notes(skipped: list[SkippedPrFile]) -> list[str]:
    notes = [
        "PR static analysis is file-scoped in this mode; mypy is skipped until full-checkout review is added."
    ]
    if skipped:
        rendered = ", ".join(f"{item.path} ({item.reason})" for item in skipped)
        notes.append(f"Skipped Python file(s): {rendered}.")
    return notes


def cmd_review_pr(args: argparse.Namespace) -> int:
    config = _load_config_for_args(args)
    if config is None:
        return 1

    try:
        owner, repo, number = parse_pr_ref(args.pr)
        client = _github_client()
    except GithubError as exc:
        console.print("[red]GitHub error.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 1

    pr_ref = f"{owner}/{repo}#{number}"
    expected_head_sha = _expected_pr_head_sha(args)
    console.print(f"\n[bold]prr[/bold] reviewing PR [cyan]{pr_ref}[/cyan] …\n")

    try:
        head_sha = _pr_head_sha(client.get_pr(owner, repo, number))
        if expected_head_sha is not None and _head_changed(expected_head_sha, head_sha, pr_ref):
            return 0
        pr_files = client.list_pr_files(owner, repo, number)
    except (GithubError, KeyError, TypeError) as exc:
        console.print("[red]Could not fetch PR from GitHub.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 2

    targets, skipped = _pr_review_targets(pr_files, config)
    notes = _pr_review_notes(skipped)
    if not targets:
        console.print("[dim]No reviewable Python files in this PR.[/dim]")
        for item in skipped:
            console.print(f"[dim]Skipped {item.path}: {item.reason}.[/dim]")
        if skipped and not args.dry_run:
            try:
                post_head_sha = _refresh_pr_head_sha(
                    client, owner, repo, number, head_sha, pr_ref
                )
                if post_head_sha is None:
                    return 0
                result = client.post_review(
                    owner,
                    repo,
                    number,
                    build_summary([], notes=notes),
                    [],
                    commit_id=post_head_sha,
                )
            except GithubError as exc:
                console.print("[red]Could not post review to GitHub.[/red]")
                console.print(f"[dim]{exc}[/dim]")
                return 2
            except (KeyError, TypeError) as exc:
                console.print("[red]Could not refresh PR head from GitHub.[/red]")
                console.print(f"[dim]{exc}[/dim]")
                return 2
            url = str(result.get("html_url") or "")
            console.print(f"[green]Review posted.[/green] {url}".rstrip())
        elif skipped:
            console.print("[dim]Dry run — review not posted.[/dim]")
        return 0

    review_head_sha = head_sha
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        local_files: list[Path] = []
        try:
            refreshed = _refresh_pr_head_sha(
                client, owner, repo, number, head_sha, pr_ref
            )
            if refreshed is None:
                return 0
            review_head_sha = refreshed
        except (GithubError, KeyError, TypeError) as exc:
            console.print("[red]Could not refresh PR head from GitHub.[/red]")
            console.print(f"[dim]{exc}[/dim]")
            return 2

        for target in targets:
            try:
                content = client.get_file_content(
                    owner, repo, target.path, review_head_sha
                )
            except GithubError as exc:
                console.print(f"[red]Could not fetch file from GitHub:[/red] {target.path}")
                console.print(f"[dim]{exc}[/dim]")
                return 2
            local = workspace / target.path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(content, encoding="utf-8")
            local_files.append(local)

        static_findings = _run_static_tools(local_files, root=workspace, tools=_PR_STATIC_TOOLS)

        llm_findings: list[Finding] = []
        for target, local in zip(targets, local_files):
            console.print(f"[dim]file {target.path}[/dim]")
            found, ok, _ = _review_file(
                local,
                config,
                static_findings,
                root=workspace,
                only_lines=target.patch_info.added_lines,
            )
            if not ok:
                return 2
            llm_findings.extend(found)

        findings = filter_findings(
            [*static_findings, *llm_findings],
            config=config,
            root=workspace,
            allowed_lines={target.path: target.patch_info.added_lines for target in targets},
        )

    kept, dropped = _cap_pr_findings(findings, config)
    comments = [build_comment(finding) for finding in kept]
    summary = build_summary(kept, dropped_count=dropped, notes=notes)

    console.print()
    for item in skipped:
        console.print(f"[yellow]Skipped {item.path}:[/yellow] {item.reason}.")
    if not kept:
        console.print(_PURR, style="green")
    else:
        console.print(_SWAT, style="red")
        console.print(
            f"[bold]{len(kept)} comment(s)[/bold] for [cyan]{owner}/{repo}#{number}[/cyan]\n"
        )
        for finding in kept:
            console.print(f"[bold cyan]{finding.path}[/bold cyan]")
            _render_finding(finding)
            console.print()

    has_errors = any(finding.severity == "error" for finding in kept)

    if args.dry_run:
        console.print("[dim]Dry run — review not posted.[/dim]")
        return _pr_exit_code(has_errors, args)

    try:
        post_head_sha = _refresh_pr_head_sha(
            client, owner, repo, number, review_head_sha, pr_ref
        )
        if post_head_sha is None:
            return 0
        result = client.post_review(
            owner,
            repo,
            number,
            summary,
            comments,
            commit_id=post_head_sha,
        )
    except GithubError as exc:
        console.print("[red]Could not post review to GitHub.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 2
    except (KeyError, TypeError) as exc:
        console.print("[red]Could not refresh PR head from GitHub.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 2

    url = str(result.get("html_url") or "")
    console.print(f"[green]Review posted.[/green] {url}".rstrip())
    return _pr_exit_code(has_errors, args)


# ── argument parsing ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prr",
        description="Local code-review cat 🐈",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # prr review <file> | prr review --pr owner/repo#n
    p_review = sub.add_parser("review", help="Review a single file or a GitHub PR")
    p_review.add_argument("file", nargs="?", help="Python file to review")
    p_review.add_argument("--pr", help="GitHub pull request, e.g. owner/repo#123")
    p_review.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the PR review but do not post it to GitHub",
    )
    p_review.add_argument("--config", help="Path to config.yaml")

    # prr scan <path>
    p_scan = sub.add_parser("scan", help="Scan a Python file or directory")
    p_scan.add_argument("path", help="Python file or directory to scan")
    p_scan.add_argument("--config", help="Path to config.yaml")

    # prr eval
    p_eval = sub.add_parser("eval", help="Run seeded regression eval cases")
    p_eval.add_argument(
        "--cases",
        help="Path to an eval cases manifest; defaults to built-in synthetic cases",
    )
    p_eval.add_argument("--config", help="Path to config.yaml")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "review":
        sys.exit(cmd_review(args))
    elif args.command == "scan":
        sys.exit(cmd_scan(args))
    elif args.command == "eval":
        sys.exit(cmd_eval(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
