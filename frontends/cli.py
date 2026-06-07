"""prr CLI — Week 1: prr review <file>

Subparser structure leaves room for:
  prr scan <path>       (Week 2)
  prr review --pr ...   (Week 3)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core.ingest import chunk_file
from core.model import MODEL, ModelBackendError, review
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


def cmd_review(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        return 1

    console.print(f"\n[bold]prr[/bold] reviewing [cyan]{path}[/cyan] …\n")

    chunks = chunk_file(path)
    if not chunks:
        console.print("[dim]File is empty.[/dim]")
        return 0

    all_findings: list[Finding] = []
    for chunk in chunks:
        console.print(
            f"  [dim]→ {chunk.kind} [bold]{chunk.name}[/bold]"
            f"  (lines {chunk.start_line}–{chunk.end_line})[/dim]"
        )
        try:
            found = review(
                code=chunk.code,
                path=str(path),
                start_line=chunk.start_line,
            )
        except ModelBackendError as exc:
            console.print("[red]Model backend failed.[/red]")
            console.print(f"[dim]{exc}[/dim]")
            console.print(
                "[dim]Start Ollama and make sure the model is available: "
                f"ollama pull {MODEL}[/dim]"
            )
            return 2
        all_findings.extend(found)

    console.print()

    if not all_findings:
        console.print(_PURR, style="green")
        return 0

    # Sort: errors first, then warnings, then info; within severity by line
    order = {"error": 0, "warning": 1, "info": 2}
    all_findings.sort(key=lambda f: (order[f.severity], f.line))

    console.print(_SWAT, style="red")
    console.print(
        f"[bold]{len(all_findings)} finding(s)[/bold] in [cyan]{path}[/cyan]\n"
    )
    for f in all_findings:
        _render_finding(f)
        console.print()

    return 1 if any(f.severity == "error" for f in all_findings) else 0


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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "review":
        sys.exit(cmd_review(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
