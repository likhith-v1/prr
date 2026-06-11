"""GitHub PR output — fetch PR data and post one batched inline review.

Findings are rendered as review comments anchored on the RIGHT (new) side of
the diff; `suggestion` fields become one-click ```suggestion``` blocks.  The
review is posted in a single API call with a cat-verdict summary body.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

import httpx

from core.schema import MOOD, Finding


_API_VERSION = "2022-11-28"
_PR_REF = re.compile(r"^(?P<owner>[^/#\s]+)/(?P<repo>[^/#\s]+)#(?P<number>\d+)$")
_PER_PAGE = 100


class GithubError(RuntimeError):
    """Raised when the GitHub API cannot be reached or rejects a request."""


def parse_pr_ref(ref: str) -> tuple[str, str, int]:
    """Parse 'owner/repo#123' into (owner, repo, number)."""
    match = _PR_REF.match(ref.strip())
    if match is None:
        raise GithubError(
            f"Invalid PR reference {ref!r}; expected the form owner/repo#number"
        )
    return match.group("owner"), match.group("repo"), int(match.group("number"))


class GithubClient:
    """Minimal GitHub REST client for PR review (fetch files, post review)."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        http_client: httpx.Client | None = None,
    ) -> None:
        if token is None:
            token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise GithubError(
                "No GitHub token found. Set the GITHUB_TOKEN environment variable "
                "to a PAT with pull-request access."
            )
        self._client = http_client or httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method, url, headers=headers, params=params, json=json
            )
        except httpx.HTTPError as exc:
            raise GithubError(f"GitHub request failed: {exc}") from exc
        if response.status_code >= 400:
            raise GithubError(
                f"GitHub API {method} {url} returned {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response

    def get_pr(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}").json()

    def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{number}/files",
                params={"per_page": _PER_PAGE, "page": page},
            ).json()
            if not isinstance(batch, list):
                raise GithubError("Unexpected response listing PR files")
            files.extend(batch)
            if len(batch) < _PER_PAGE:
                return files
            page += 1

    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        response = self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            headers={"Accept": "application/vnd.github.raw+json"},
            params={"ref": ref},
        )
        return response.text

    def post_review(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"body": body, "event": "COMMENT", "comments": comments},
        ).json()


# ── payload builders (pure) ───────────────────────────────────────────────────

def build_comment(finding: Finding) -> dict[str, Any]:
    """Render one Finding as a GitHub review comment dict.

    For ranges GitHub anchors the comment at the END line: start_line is the
    first line of the range and line is the last (start_line < line, both on
    the RIGHT side); reversing them is the classic 422.
    """
    body_parts = [f"{MOOD[finding.severity]} **[{finding.category}]** {finding.comment}"]
    if finding.suggestion is not None:
        body_parts.append(f"```suggestion\n{finding.suggestion}\n```")

    comment: dict[str, Any] = {
        "path": finding.path,
        "side": "RIGHT",
        "body": "\n\n".join(body_parts),
    }
    if finding.end_line is not None and finding.end_line > finding.line:
        comment["start_line"] = finding.line
        comment["start_side"] = "RIGHT"
        comment["line"] = finding.end_line
    else:
        comment["line"] = finding.line
    return comment


def build_summary(
    findings: list[Finding],
    dropped_count: int = 0,
    notes: list[str] | None = None,
) -> str:
    """Cat-verdict review body: purring when clean, otherwise severity counts."""
    if notes is None:
        notes = []

    if not findings and not dropped_count:
        lines = ["😸 **prr is purring** — nothing to flag in this PR."]
        lines.extend(f"- {note}" for note in notes)
        return "\n\n".join(lines)

    counts = Counter(finding.severity for finding in findings)
    rendered = ", ".join(
        f"{counts[severity]} {severity}"
        for severity in ("error", "warning", "info")
        if counts[severity]
    )
    lines = [f"🙀 **prr is not happy** — {rendered or 'see below'}."]
    if counts["error"]:
        lines.append("At least one finding looks like a real bug — please take a look.")
    else:
        lines.append("Nothing fatal, but worth a look before merging.")
    if dropped_count:
        lines.append(
            f"_{dropped_count} lower-priority finding(s) were hidden by the per-PR comment cap._"
        )
    lines.extend(f"- {note}" for note in notes)
    return "\n\n".join(lines)
