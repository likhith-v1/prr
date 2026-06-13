"""GitHub Actions entrypoint for self-hosted PR review."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping

from rich.console import Console

from frontends.cli import cmd_review


console = Console()


class ActionEnvError(RuntimeError):
    """Raised when the workflow environment is missing required PR context."""


def pr_ref_from_env(env: Mapping[str, str] | None = None) -> str:
    """Build owner/repo#number from GitHub Actions environment variables."""
    if env is None:
        env = os.environ

    repository = env.get("GITHUB_REPOSITORY", "").strip()
    if repository.count("/") != 1:
        raise ActionEnvError("GITHUB_REPOSITORY must be set to owner/repo")

    raw_number = env.get("PR_NUMBER", "").strip()
    try:
        number = int(raw_number)
    except ValueError as exc:
        raise ActionEnvError("PR_NUMBER must be set to a positive integer") from exc
    if number < 1:
        raise ActionEnvError("PR_NUMBER must be set to a positive integer")

    return f"{repository}#{number}"


def pr_head_sha_from_env(env: Mapping[str, str] | None = None) -> str:
    """Read the exact triggering PR head commit from the workflow environment."""
    if env is None:
        env = os.environ

    head_sha = env.get("PR_HEAD_SHA", "").strip()
    if not head_sha:
        raise ActionEnvError("PR_HEAD_SHA must be set to the triggering PR head SHA")
    return head_sha


def run(env: Mapping[str, str] | None = None) -> int:
    try:
        pr_ref = pr_ref_from_env(env)
        head_sha = pr_head_sha_from_env(env)
    except ActionEnvError as exc:
        console.print("[red]GitHub Actions environment error.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        return 2

    return cmd_review(
        argparse.Namespace(
            file=None,
            pr=pr_ref,
            dry_run=False,
            config=None,
            no_fail_on_findings=True,
            pr_head_sha=head_sha,
        )
    )


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
