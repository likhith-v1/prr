from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from core.config import PrrConfig
from core.eval import (
    CaseEvalResult,
    EvalCase,
    EvalError,
    EvalModelError,
    EvalReport,
    ExpectedIssue,
    MissedIssue,
)
from core.model import ModelBackendError
from core.schema import Finding
from frontends.cli import (
    SkippedPrFile,
    _pr_review_targets,
    build_parser,
    cmd_eval,
    cmd_review,
    cmd_scan,
)


class FakeGithubClient:
    def __init__(self, files: list[dict[str, object]], contents: dict[str, str]) -> None:
        self.files = files
        self.contents = contents
        self.fetched: list[tuple[str, str]] = []
        self.posted: list[dict[str, object]] = []

    def get_pr(self, owner: str, repo: str, number: int) -> dict[str, object]:
        return {"head": {"sha": "abc123"}}

    def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict[str, object]]:
        return self.files

    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        self.fetched.append((path, ref))
        return self.contents[path]

    def post_review(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        comments: list[dict[str, object]],
        commit_id: str | None = None,
    ) -> dict[str, object]:
        self.posted.append({
            "number": number,
            "body": body,
            "comments": comments,
            "commit_id": commit_id,
        })
        return {"html_url": "https://example.test/review"}


_PR_CONTENT = "def f():\n    x = eval(data)\n    return x\ny = 1\n"
_PR_PATCH = "@@ -1,2 +1,3 @@\n def f():\n+    x = eval(data)\n     return x"


def make_pr_client() -> FakeGithubClient:
    return FakeGithubClient(
        files=[
            {"filename": "pkg/sample.py", "status": "modified", "patch": _PR_PATCH},
            {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
            {"filename": "gone.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"},
            {"filename": "big.py", "status": "modified"},
        ],
        contents={"pkg/sample.py": _PR_CONTENT},
    )


class CliTests(unittest.TestCase):
    def test_review_warns_when_static_tools_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            output = io.StringIO()

            with (
                patch("core.detect_static._resolve_executable", return_value=None),
                patch("frontends.cli.review", return_value=[]),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_review(argparse.Namespace(file=str(path), config=None))

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Static analysis skipped", rendered)
        self.assertIn("ruff not found", rendered)

    def test_model_backend_error_is_user_facing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            output = io.StringIO()

            with (
                patch("frontends.cli._run_static_tools", return_value=[]),
                patch("frontends.cli.review", side_effect=ModelBackendError("no ollama")),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_review(argparse.Namespace(file=str(path)))

        rendered = output.getvalue()
        self.assertEqual(result, 2)
        self.assertIn("Model backend failed.", rendered)
        self.assertIn("ollama pull qwen2.5-coder:14b", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_review_rejects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()

            with patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ):
                result = cmd_review(argparse.Namespace(file=tmp, config=None))

        self.assertEqual(result, 1)
        self.assertIn("Not a file", output.getvalue())

    def test_scan_uses_configured_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            config_path = root / "config.yaml"
            config_path.write_text("model: test-model\n", encoding="utf-8")
            output = io.StringIO()
            calls: list[dict[str, object]] = []

            def fake_review(**kwargs: object) -> list[Finding]:
                calls.append(kwargs)
                return []

            with (
                patch("frontends.cli._run_static_tools", return_value=[]),
                patch("frontends.cli.review", side_effect=fake_review),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=str(config_path)))

        self.assertEqual(result, 0)
        self.assertEqual(calls[0]["model"], "test-model")
        self.assertIn("prr is purring", output.getvalue())

    def test_scan_reports_static_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("print(nam)\n", encoding="utf-8")
            static = Finding(
                path=str(path),
                line=1,
                severity="error",
                category="bug",
                comment="F821: Undefined name `nam`",
                source="ruff",
            )
            output = io.StringIO()

            with (
                patch("frontends.cli._run_static_tools", return_value=[static]),
                patch("frontends.cli.review", return_value=[]),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=None))

        rendered = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("sample.py", rendered)
        self.assertIn("Undefined name", rendered)

    def test_scan_respects_ignore_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = root / "sample.py"
            ignored_dir = root / ".venv"
            ignored = ignored_dir / "ignored.py"
            included.write_text("x = 1\n", encoding="utf-8")
            ignored_dir.mkdir()
            ignored.write_text("raise RuntimeError('skip me')\n", encoding="utf-8")
            nested_dir = root / "sub" / ".venv"
            nested_dir.mkdir(parents=True)
            nested = nested_dir / "nested.py"
            nested.write_text("raise RuntimeError('skip me too')\n", encoding="utf-8")
            output = io.StringIO()
            static_calls: list[list[Path]] = []
            review_calls: list[str] = []

            def fake_static(paths: list[Path], root: Path) -> list[Finding]:
                static_calls.append(paths)
                return []

            def fake_review(**kwargs: object) -> list[Finding]:
                review_calls.append(str(kwargs["path"]))
                return []

            with (
                patch("frontends.cli._run_static_tools", side_effect=fake_static),
                patch("frontends.cli.review", side_effect=fake_review),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=None))

        self.assertEqual(result, 0)
        self.assertEqual(static_calls, [[included]])
        self.assertEqual(review_calls, [str(included)])
        self.assertIn("prr is purring", output.getvalue())

    def test_review_rejects_file_and_pr_together(self) -> None:
        output = io.StringIO()

        with patch(
            "frontends.cli.console",
            Console(file=output, force_terminal=False, color_system=None),
        ):
            result = cmd_review(
                argparse.Namespace(file="x.py", pr="o/r#1", dry_run=False, config=None)
            )

        self.assertEqual(result, 1)
        self.assertIn("not both", output.getvalue())

    def test_review_requires_file_or_pr(self) -> None:
        output = io.StringIO()

        with patch(
            "frontends.cli.console",
            Console(file=output, force_terminal=False, color_system=None),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr=None, dry_run=False, config=None)
            )

        self.assertEqual(result, 1)

    def test_pr_review_posts_single_batched_review(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        def fake_review(**kwargs: object) -> list[Finding]:
            return [
                Finding(
                    path=str(kwargs["path"]),
                    line=2,
                    severity="error",
                    category="security",
                    comment="eval of untrusted input.",
                    suggestion="    x = int(data)",
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
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=False, config=None)
            )

        self.assertEqual(result, 1)
        self.assertEqual(client.fetched, [("pkg/sample.py", "abc123")])
        self.assertEqual(len(client.posted), 1)
        review_payload = client.posted[0]
        self.assertEqual(review_payload["commit_id"], "abc123")
        self.assertIn("prr is not happy", str(review_payload["body"]))
        comments = review_payload["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["path"], "pkg/sample.py")
        self.assertEqual(comments[0]["line"], 2)
        self.assertEqual(comments[0]["side"], "RIGHT")
        self.assertIn("```suggestion\n    x = int(data)\n```", comments[0]["body"])

    def test_pr_review_can_ignore_findings_for_action_exit_code(self) -> None:
        client = make_pr_client()
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
        ):
            result = cmd_review(
                argparse.Namespace(
                    file=None,
                    pr="octo/prr#7",
                    dry_run=False,
                    config=None,
                    no_fail_on_findings=True,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(client.posted), 1)
        self.assertIn("prr is not happy", str(client.posted[0]["body"]))

    def test_pr_review_skips_when_triggering_head_is_stale(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        with (
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(
                    file=None,
                    pr="octo/prr#7",
                    dry_run=False,
                    config=None,
                    pr_head_sha="old123",
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.fetched, [])
        self.assertEqual(client.posted, [])
        self.assertIn("Skipping review", output.getvalue())

    def test_pr_review_skips_post_when_head_moves_during_review(self) -> None:
        class MovingHeadClient(FakeGithubClient):
            def __init__(self) -> None:
                super().__init__(
                    files=[
                        {"filename": "pkg/sample.py", "status": "modified", "patch": _PR_PATCH}
                    ],
                    contents={"pkg/sample.py": _PR_CONTENT},
                )
                self.heads = ["abc123", "abc123", "new456"]

            def get_pr(self, owner: str, repo: str, number: int) -> dict[str, object]:
                return {"head": {"sha": self.heads.pop(0)}}

        client = MovingHeadClient()
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
        ):
            result = cmd_review(
                argparse.Namespace(
                    file=None,
                    pr="octo/prr#7",
                    dry_run=False,
                    config=None,
                    pr_head_sha="abc123",
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.posted, [])
        self.assertIn("Skipping review", output.getvalue())

    def test_pr_review_skips_post_when_head_moves_during_local_review(self) -> None:
        class MovingHeadClient(FakeGithubClient):
            def __init__(self) -> None:
                super().__init__(
                    files=[
                        {"filename": "pkg/sample.py", "status": "modified", "patch": _PR_PATCH}
                    ],
                    contents={"pkg/sample.py": _PR_CONTENT},
                )
                self.heads = ["abc123", "abc123", "new456"]

            def get_pr(self, owner: str, repo: str, number: int) -> dict[str, object]:
                return {"head": {"sha": self.heads.pop(0)}}

        client = MovingHeadClient()
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
        ):
            result = cmd_review(
                argparse.Namespace(
                    file=None,
                    pr="octo/prr#7",
                    dry_run=False,
                    config=None,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.posted, [])
        self.assertIn("Skipping review", output.getvalue())

    def test_pr_review_drops_findings_outside_diff(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        def fake_review(**kwargs: object) -> list[Finding]:
            # Line 4 exists in the file but is outside the diff hunks.
            return [
                Finding(
                    path=str(kwargs["path"]),
                    line=4,
                    severity="error",
                    category="bug",
                    comment="Hallucinated finding outside the diff.",
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
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=False, config=None)
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0]["comments"], [])
        self.assertIn("prr is purring", str(client.posted[0]["body"]))

    def test_pr_review_drops_findings_on_unchanged_context_lines(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        def fake_review(**kwargs: object) -> list[Finding]:
            # Line 3 is in the diff hunk as unchanged context, not an added line.
            return [
                Finding(
                    path=str(kwargs["path"]),
                    line=3,
                    severity="error",
                    category="bug",
                    comment="Pre-existing context line.",
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
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=False, config=None)
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.posted[0]["comments"], [])

    def test_pr_review_reports_patchless_python_files(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        with (
            patch("frontends.cli._run_static_tools", return_value=[]),
            patch("frontends.cli.review", return_value=[]),
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=False, config=None)
            )

        self.assertEqual(result, 0)
        self.assertIn("Skipped big.py", output.getvalue())
        self.assertIn("big.py", str(client.posted[0]["body"]))
        self.assertIn("no usable GitHub patch", str(client.posted[0]["body"]))

    def test_pr_review_posts_summary_when_all_python_files_are_skipped(self) -> None:
        client = FakeGithubClient(
            files=[{"filename": "big.py", "status": "modified"}],
            contents={},
        )
        output = io.StringIO()

        with (
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=False, config=None)
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0]["commit_id"], "abc123")
        self.assertEqual(client.posted[0]["comments"], [])
        self.assertIn("big.py", str(client.posted[0]["body"]))
        self.assertIn("Review posted", output.getvalue())

    def test_pr_review_skips_mypy_in_temp_workspace_static_pass(self) -> None:
        client = make_pr_client()
        calls: list[tuple[str, ...]] = []
        output = io.StringIO()

        def fake_static(paths: list[Path], root: Path, tools: tuple[str, ...]) -> list[Finding]:
            calls.append(tools)
            return []

        with (
            patch("frontends.cli._run_static_tools", side_effect=fake_static),
            patch("frontends.cli.review", return_value=[]),
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=True, config=None)
            )

        self.assertEqual(result, 0)
        self.assertEqual(calls, [("ruff", "bandit")])

    def test_pr_review_dry_run_does_not_post(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        with (
            patch("frontends.cli._run_static_tools", return_value=[]),
            patch("frontends.cli.review", return_value=[]),
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=True, config=None)
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.posted, [])
        self.assertIn("Dry run", output.getvalue())

    def test_pr_review_dry_run_prints_summary(self) -> None:
        client = make_pr_client()
        output = io.StringIO()

        def fake_review(**kwargs: object) -> list[Finding]:
            return [
                Finding(
                    path=str(kwargs["path"]),
                    line=2,
                    severity="warning",
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
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=True, config=None)
            )

        rendered = output.getvalue()
        normalized = " ".join(rendered.split())
        self.assertEqual(result, 0)
        self.assertEqual(client.posted, [])
        self.assertIn("mypy is skipped until full-checkout review is added", normalized)
        self.assertTrue(
            "prr is purring" in normalized or "prr is not happy" in normalized,
            msg=rendered,
        )
        self.assertIn("Review body that would be posted:", rendered)

    def test_pr_review_targets_skips_non_python_files(self) -> None:
        pr_files = [
            {
                "filename": "src/utils.ts",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n-a\n+b",
            },
            {
                "filename": "src/app.py",
                "status": "modified",
                "patch": "@@ -1 +1,2 @@\n x = 1\n+x = 2",
            },
        ]

        targets, skipped = _pr_review_targets(pr_files, PrrConfig())

        self.assertEqual(
            skipped,
            [
                SkippedPrFile(
                    "src/utils.ts",
                    "non-Python file, language not yet supported",
                )
            ],
        )
        self.assertEqual([target.path for target in targets], ["src/app.py"])

    def test_pr_review_dry_run_reports_skipped_non_python_files(self) -> None:
        client = FakeGithubClient(
            files=[
                {"filename": "src/utils.ts", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
                {"filename": "pkg/sample.py", "status": "modified", "patch": _PR_PATCH},
            ],
            contents={"pkg/sample.py": _PR_CONTENT},
        )
        output = io.StringIO()

        with (
            patch("frontends.cli._run_static_tools", return_value=[]),
            patch("frontends.cli.review", return_value=[]),
            patch("frontends.cli._github_client", return_value=client),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_review(
                argparse.Namespace(file=None, pr="octo/prr#7", dry_run=True, config=None)
            )

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(client.posted, [])
        self.assertIn("src/utils.ts", rendered)
        self.assertIn("non-Python file, language not yet supported", rendered)

    def test_scan_invalid_config_is_user_facing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            config_path.write_text("unknown: true\n", encoding="utf-8")
            output = io.StringIO()

            with patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=str(config_path)))

        self.assertEqual(result, 1)
        self.assertIn("Config error", output.getvalue())

    def test_parser_accepts_eval_command(self) -> None:
        args = build_parser().parse_args([
            "eval",
            "--cases",
            "cases.yaml",
            "--config",
            "config.yaml",
        ])

        self.assertEqual(args.command, "eval")
        self.assertEqual(args.cases, "cases.yaml")
        self.assertEqual(args.config, "config.yaml")

    def test_eval_command_reports_regression_exit_code(self) -> None:
        expected = ExpectedIssue(line=2, category="bug", min_severity="warning")
        report = EvalReport([
            CaseEvalResult(
                case=EvalCase(
                    id="sample",
                    path="sample.py",
                    source="sample.py.txt",
                    expected=[expected],
                ),
                findings=[],
                caught=[],
                missed=[MissedIssue("sample", expected)],
                false_positives=[],
            )
        ])
        output = io.StringIO()

        with (
            patch("frontends.cli.run_eval", return_value=report),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_eval(argparse.Namespace(cases=None, config=None))

        self.assertEqual(result, 1)
        self.assertIn("missed 1", output.getvalue())

    def test_eval_command_reports_runtime_failure(self) -> None:
        output = io.StringIO()

        with (
            patch("frontends.cli.run_eval", side_effect=EvalError("boom")),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_eval(argparse.Namespace(cases=None, config=None))

        self.assertEqual(result, 2)
        self.assertIn("Eval failed", output.getvalue())

    def test_eval_command_reports_model_failure_with_ollama_hint(self) -> None:
        output = io.StringIO()

        with (
            patch("frontends.cli.run_eval", side_effect=EvalModelError("no ollama")),
            patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ),
        ):
            result = cmd_eval(argparse.Namespace(cases=None, config=None))

        rendered = output.getvalue()
        self.assertEqual(result, 2)
        self.assertIn("Eval failed", rendered)
        self.assertIn("ollama pull qwen2.5-coder:14b", rendered)


if __name__ == "__main__":
    unittest.main()
