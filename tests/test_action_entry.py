from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from rich.console import Console

from core.schema import Finding
from frontends.action_entry import (
    ActionEnvError,
    pr_head_sha_from_env,
    pr_ref_from_env,
    run,
)


class FakeGithubClient:
    def __init__(self) -> None:
        self.posted: list[dict[str, object]] = []

    def get_pr(self, owner: str, repo: str, number: int) -> dict[str, object]:
        return {"head": {"sha": "abc123"}}

    def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict[str, object]]:
        return [
            {
                "filename": "pkg/sample.py",
                "status": "modified",
                "patch": "@@ -1,2 +1,3 @@\n def f():\n+    x = eval(data)\n     return x",
            }
        ]

    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        return "def f():\n    x = eval(data)\n    return x\n"

    def post_review(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        comments: list[dict[str, object]],
        commit_id: str | None = None,
    ) -> dict[str, object]:
        self.posted.append({"body": body, "comments": comments, "commit_id": commit_id})
        return {"html_url": "https://example.test/review"}


class ActionEntryTests(unittest.TestCase):
    def test_missing_repository_env_is_rejected(self) -> None:
        with self.assertRaises(ActionEnvError):
            pr_ref_from_env({"PR_NUMBER": "1"})

    def test_malformed_pr_number_is_rejected(self) -> None:
        with self.assertRaises(ActionEnvError):
            pr_ref_from_env({"GITHUB_REPOSITORY": "octo/prr", "PR_NUMBER": "nope"})

    def test_missing_head_sha_is_rejected(self) -> None:
        with self.assertRaises(ActionEnvError):
            pr_head_sha_from_env({"GITHUB_REPOSITORY": "octo/prr", "PR_NUMBER": "7"})

    def test_builds_pr_ref_from_env(self) -> None:
        self.assertEqual(
            pr_ref_from_env({"GITHUB_REPOSITORY": "octo/prr", "PR_NUMBER": "7"}),
            "octo/prr#7",
        )

    def test_action_stays_green_when_prr_finds_errors(self) -> None:
        client = FakeGithubClient()
        output = io.StringIO()

        def fake_review(**kwargs: object) -> list[Finding]:
            return [
                Finding(
                    path=str(kwargs["path"]),
                    line=2,
                    severity="error",
                    category="security",
                    comment="eval of untrusted input.",
                    source="llm",
                    confidence=0.95,
                )
            ]

        with (
            patch("frontends.cli._run_static_tools", return_value=[]),
            patch("frontends.cli.review", side_effect=fake_review),
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
            patch(
                "frontends.action_entry.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = run({
                "GITHUB_REPOSITORY": "octo/prr",
                "PR_NUMBER": "7",
                "PR_HEAD_SHA": "abc123",
            })

        self.assertEqual(result, 0)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0]["commit_id"], "abc123")
        self.assertIn("prr is not happy", str(client.posted[0]["body"]))


if __name__ == "__main__":
    unittest.main()
