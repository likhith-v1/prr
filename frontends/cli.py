"""prr CLI — Week 1: prr review <file>

Subparser structure leaves room for:
  prr scan <path>       (Week 2)
  prr review --pr ...   (Week 3)
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core.config import ConfigError, PrrConfig, load_config
from core.context import build_context, findings_for_chunk
from core.detect_static import run_static_tools
from core.filter import filter_findings
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
) -> tuple[list[Finding], bool]:
    try:
        chunks = chunk_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        console.print(f"[red]Could not chunk file:[/red] {path}")
        console.print(f"[dim]{exc}[/dim]")
        return [], False

    if not chunks:
        return [], True

    all_findings: list[Finding] = []
    for chunk in chunks:
        console.print(
            f"  [dim]→ {chunk.kind} [bold]{chunk.name}[/bold]"
            f"  (lines {chunk.start_line}–{chunk.end_line})[/dim]"
        )
        prior = findings_for_chunk(chunk, static_findings)
        try:
            found = review(
                code=chunk.code,
                path=str(path),
                start_line=chunk.start_line,
                context=build_context(chunk, static_findings),
                findings=prior,
                model=config.model,
            )
        except ModelBackendError as exc:
            console.print("[red]Model backend failed.[/red]")
            console.print(f"[dim]{exc}[/dim]")
            console.print(
                "[dim]Start Ollama and make sure the model is available: "
                f"ollama pull {config.model}[/dim]"
            )
            return [], False
        all_findings.extend(found)
    return all_findings, True


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
    config = _load_config_for_args(args)
    if config is None:
        return 1

    path = Path(args.file)
    if not _validate_python_file(path):
        return 1

    console.print(f"\n[bold]prr[/bold] reviewing [cyan]{path}[/cyan] …\n")

    static_findings = run_static_tools([path], root=Path.cwd())
    llm_findings, ok = _review_file(path, config, static_findings)
    if not ok:
        return 2

    all_findings = filter_findings(
        [*static_findings, *llm_findings],
        config=config,
        root=Path.cwd(),
    )
    if not all_findings and not chunk_file(path):
        console.print("[dim]File is empty.[/dim]")
        return 0

    return _render_findings(all_findings, path)


def _is_ignored(path: Path, root: Path, config: PrrConfig) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(fnmatch.fnmatch(rel, pattern) for pattern in config.ignore_paths)


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
    static_findings = run_static_tools(files, root=root)

    llm_findings: list[Finding] = []
    for path in files:
        console.print(f"[dim]file {path}[/dim]")
        found, ok = _review_file(path, config, static_findings)
        if not ok:
            return 2
        llm_findings.extend(found)

    findings = filter_findings(
        [*static_findings, *llm_findings],
        config=config,
        root=root,
    )
    return _render_scan_findings(findings, target)


# ── argument parsing ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prr",
        description="Local code-review cat 🐈",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # prr review <file>
    p_review = sub.add_parser("review", help="Review a single file")
    p_review.add_argument("file", help="Python file to review")
    p_review.add_argument("--config", help="Path to config.yaml")

    # prr scan <path>
    p_scan = sub.add_parser("scan", help="Scan a Python file or directory")
    p_scan.add_argument("path", help="Python file or directory to scan")
    p_scan.add_argument("--config", help="Path to config.yaml")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "review":
        sys.exit(cmd_review(args))
    elif args.command == "scan":
        sys.exit(cmd_scan(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
