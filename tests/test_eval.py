from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from core.config import PrrConfig
from core.detect_static import StaticToolsResult
from core.eval import EvalError, EvalModelError, load_eval_cases, run_eval
from core.model import ModelBackendError
from core.schema import Finding


def _fake_review_for_expected(**kwargs: object) -> list[Finding]:
    path = Path(str(kwargs["path"]))
    cases, _ = load_eval_cases(None)
    case = next(item for item in cases if item.path == path.name)
    return [
        Finding(
            path=str(path),
            line=expected.line,
            severity=expected.min_severity,
            category=expected.category,
            comment=f"Expected issue at line {expected.line}.",
            source="llm",
        )
        for expected in case.expected
    ]


def write_manifest(root: Path, source: str, expected: str) -> Path:
    fixture = root / "fixtures" / "sample.py.txt"
    fixture.parent.mkdir()
    fixture.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    expected_block = textwrap.indent(textwrap.dedent(expected).strip() + "\n", "      ")
    manifest = root / "cases.yaml"
    manifest.write_text(
        "cases:\n"
        "  - id: sample\n"
        "    path: sample.py\n"
        "    source: fixtures/sample.py.txt\n"
        "    expected:\n"
        f"{expected_block}",
        encoding="utf-8",
    )
    return manifest


class EvalTests(unittest.TestCase):
    def test_loads_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root,
                """
                def f(data):
                    return eval(data)
                """,
                """
                - line: 2
                  category: security
                  min_severity: error
                """,
            )

            cases, source_root = load_eval_cases(manifest)

        self.assertEqual(source_root, root)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].id, "sample")
        self.assertEqual(cases[0].expected[0].category, "security")

    def test_empty_manifest_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "cases.yaml"
            manifest.write_text("cases: []\n", encoding="utf-8")

            with self.assertRaises(EvalError) as ctx:
                load_eval_cases(manifest)

        self.assertIn("Invalid eval cases manifest", str(ctx.exception))

    def test_run_eval_reports_caught_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root,
                """
                def f(data):
                    return eval(data)
                """,
                """
                - line: 2
                  category: security
                  min_severity: error
                """,
            )

            def fake_review(**kwargs: object) -> list[Finding]:
                return [
                    Finding(
                        path=str(kwargs["path"]),
                        line=2,
                        severity="error",
                        category="security",
                        comment="eval of untrusted input.",
                        source="llm",
                    )
                ]

            report = run_eval(
                PrrConfig(),
                cases_path=manifest,
                review_func=fake_review,
                static_func=lambda paths, root: StaticToolsResult(findings=[]),
            )

        self.assertTrue(report.ok)
        self.assertEqual(report.caught_count, 1)
        self.assertEqual(report.missed_count, 0)
        self.assertEqual(report.false_positive_count, 0)

    def test_run_eval_reports_misses_and_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root,
                """
                def f(data):
                    return eval(data)
                """,
                """
                - line: 2
                  category: security
                  min_severity: error
                """,
            )

            def fake_review(**kwargs: object) -> list[Finding]:
                return [
                    Finding(
                        path=str(kwargs["path"]),
                        line=1,
                        severity="warning",
                        category="style",
                        comment="Unexpected nit.",
                        source="llm",
                    )
                ]

            report = run_eval(
                PrrConfig(),
                cases_path=manifest,
                review_func=fake_review,
                static_func=lambda paths, root: StaticToolsResult(findings=[]),
            )

        self.assertFalse(report.ok)
        self.assertEqual(report.missed_count, 1)
        self.assertEqual(report.false_positive_count, 1)

    def test_run_eval_wraps_model_backend_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root,
                """
                def f(data):
                    return eval(data)
                """,
                """
                - line: 2
                  category: security
                  min_severity: error
                """,
            )

            def fake_review(**kwargs: object) -> list[Finding]:
                raise ModelBackendError("no ollama")

            with self.assertRaises(EvalError) as ctx:
                run_eval(
                    PrrConfig(),
                    cases_path=manifest,
                    review_func=fake_review,
                    static_func=lambda paths, root: StaticToolsResult(findings=[]),
                )

        self.assertIsInstance(ctx.exception, EvalModelError)
        self.assertIn("Model backend failed", str(ctx.exception))

    def test_info_severity_static_noise_is_not_a_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_manifest(
                root,
                """
                def f(data):
                    return eval(data)
                """,
                """
                - line: 2
                  category: security
                  min_severity: error
                """,
            )

            def fake_review(**kwargs: object) -> list[Finding]:
                return [
                    Finding(
                        path=str(kwargs["path"]),
                        line=2,
                        severity="error",
                        category="security",
                        comment="eval of untrusted input.",
                        source="llm",
                    )
                ]

            def fake_static(paths: list[Path], root: Path) -> StaticToolsResult:
                return StaticToolsResult(findings=[
                    Finding(
                        path=str(paths[0]),
                        line=1,
                        severity="info",
                        category="security",
                        comment="B404: incidental import noise.",
                        source="bandit",
                    )
                ])

            report = run_eval(
                PrrConfig(),
                cases_path=manifest,
                review_func=fake_review,
                static_func=fake_static,
            )

        self.assertTrue(report.ok)
        self.assertEqual(report.caught_count, 1)
        self.assertEqual(report.false_positive_count, 0)

    def test_loads_built_in_manifest(self) -> None:
        cases, _ = load_eval_cases(None)

        self.assertEqual(len(cases), 8)
        self.assertEqual({case.id for case in cases}, {
            "unsafe-eval",
            "zero-division",
            "mutable-default",
            "subprocess-shell",
            "bare-except",
            "os-system-injection",
            "sql-injection",
            "open-no-encoding",
        })

    def test_built_in_cases_pass_with_fake_backend(self) -> None:
        report = run_eval(
            PrrConfig(),
            cases_path=None,
            review_func=_fake_review_for_expected,
            static_func=lambda paths, root: StaticToolsResult(findings=[]),
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.missed_count, 0)
        self.assertEqual(report.false_positive_count, 0)
        self.assertEqual(report.caught_count, report.expected_count)
        self.assertEqual(report.expected_count, 8)


if __name__ == "__main__":
    unittest.main()
