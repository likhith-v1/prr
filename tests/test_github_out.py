from __future__ import annotations

import json
import unittest

import httpx

from core.github_out import (
    GithubClient,
    GithubError,
    build_comment,
    build_summary,
    parse_pr_ref,
)
from core.schema import Finding


def make_finding(**overrides: object) -> Finding:
    data = {
        "path": "pkg/sample.py",
        "line": 3,
        "severity": "error",
        "category": "bug",
        "comment": "Division by zero when b=0.",
        "source": "llm",
        "confidence": 0.9,
    }
    data.update(overrides)
    return Finding(**data)


def make_client(handler) -> GithubClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://api.github.test", transport=transport)
    return GithubClient(token="test-token", http_client=http_client)


class PrRefTests(unittest.TestCase):
    def test_parses_valid_ref(self) -> None:
        self.assertEqual(parse_pr_ref("octo/prr#12"), ("octo", "prr", 12))

    def test_rejects_malformed_refs(self) -> None:
        for ref in ("octo/prr", "octo#12", "octo/prr#", "octo/prr#abc", ""):
            with self.assertRaises(GithubError):
                parse_pr_ref(ref)


class ClientTests(unittest.TestCase):
    def test_missing_token_is_a_friendly_error(self) -> None:
        with self.assertRaises(GithubError) as ctx:
            GithubClient(token="")
        self.assertIn("GITHUB_TOKEN", str(ctx.exception))

    def test_list_pr_files_paginates(self) -> None:
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            pages.append(page)
            if page == 1:
                batch = [{"filename": f"f{i}.py"} for i in range(100)]
            else:
                batch = [{"filename": "last.py"}]
            return httpx.Response(200, json=batch)

        files = make_client(handler).list_pr_files("o", "r", 1)

        self.assertEqual(pages, [1, 2])
        self.assertEqual(len(files), 101)

    def test_http_error_status_raises_github_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "Validation Failed"})

        with self.assertRaises(GithubError) as ctx:
            make_client(handler).get_pr("o", "r", 1)
        self.assertIn("422", str(ctx.exception))

    def test_post_review_sends_single_batched_payload(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"html_url": "https://example/review"})

        comments = [{"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"}]
        result = make_client(handler).post_review("o", "r", 7, "summary", comments)

        self.assertEqual(result["html_url"], "https://example/review")
        self.assertEqual(captured["event"], "COMMENT")
        self.assertEqual(captured["body"], "summary")
        self.assertEqual(captured["comments"], comments)
        self.assertNotIn("commit_id", captured)

    def test_post_review_can_pin_commit_id(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"html_url": "https://example/review"})

        make_client(handler).post_review("o", "r", 7, "summary", [], commit_id="abc123")

        self.assertEqual(captured["commit_id"], "abc123")


class PayloadTests(unittest.TestCase):
    def test_single_line_comment(self) -> None:
        comment = build_comment(make_finding())

        self.assertEqual(comment["path"], "pkg/sample.py")
        self.assertEqual(comment["line"], 3)
        self.assertEqual(comment["side"], "RIGHT")
        self.assertNotIn("start_line", comment)
        self.assertIn("😾", comment["body"])
        self.assertIn("Division by zero", comment["body"])
        self.assertNotIn("```suggestion", comment["body"])

    def test_ranged_comment_anchors_at_end_line(self) -> None:
        comment = build_comment(make_finding(line=3, end_line=5))

        self.assertEqual(comment["start_line"], 3)
        self.assertEqual(comment["line"], 5)
        self.assertEqual(comment["start_side"], "RIGHT")

    def test_suggestion_is_fenced(self) -> None:
        comment = build_comment(make_finding(suggestion="    return a / max(b, 1)"))

        self.assertIn("```suggestion\n    return a / max(b, 1)\n```", comment["body"])

    def test_summary_purrs_when_clean(self) -> None:
        summary = build_summary([])

        self.assertIn("prr is purring", summary)

    def test_summary_includes_notes(self) -> None:
        summary = build_summary([], notes=["Skipped pkg/large.py (no usable GitHub patch)."])

        self.assertIn("prr is purring", summary)
        self.assertIn("Skipped pkg/large.py", summary)

    def test_summary_counts_by_severity(self) -> None:
        summary = build_summary(
            [
                make_finding(severity="error"),
                make_finding(severity="warning", line=4),
                make_finding(severity="warning", line=5),
            ],
            dropped_count=2,
        )

        self.assertIn("prr is not happy", summary)
        self.assertIn("1 error", summary)
        self.assertIn("2 warning", summary)
        self.assertIn("2 lower-priority finding(s)", summary)


if __name__ == "__main__":
    unittest.main()
