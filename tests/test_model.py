from __future__ import annotations

import json
import unittest

import ollama

from core.model import OllamaBackend, _parse_findings, review


def response(*findings: dict[str, object]) -> str:
    return json.dumps({"findings": list(findings)})


def llm_finding(**overrides: object) -> dict[str, object]:
    data = {
        "line": 1,
        "severity": "error",
        "category": "bug",
        "comment": "Real issue.",
        "snippet": "return a / b",
        "suggestion": None,
        "confidence": 0.9,
    }
    data.update(overrides)
    return data


class FakeBackend:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


class ModelParsingTests(unittest.TestCase):
    def test_valid_finding_is_reanchored_by_snippet(self) -> None:
        raw = response(llm_finding(line=999))
        parsed = _parse_findings(raw, "sample.py", "def div(a, b):\nreturn a / b", 5)

        self.assertEqual(len(parsed or []), 1)
        self.assertEqual(parsed[0].line, 6)

    def test_missing_snippet_is_dropped(self) -> None:
        raw = response(llm_finding(line=999, snippet="missing"))
        parsed = _parse_findings(raw, "sample.py", "return a / b", 1)

        self.assertEqual(parsed, [])

    def test_duplicate_snippet_requires_reported_line_match(self) -> None:
        code = "x = 1\nx = 1"
        raw = response(
            llm_finding(line=2, snippet="x = 1"),
            llm_finding(line=99, snippet="x = 1"),
        )
        parsed = _parse_findings(raw, "sample.py", code, 1)

        self.assertEqual(len(parsed or []), 1)
        self.assertEqual(parsed[0].line, 2)

    def test_invalid_finding_is_dropped(self) -> None:
        raw = response(llm_finding(confidence=99.0))
        parsed = _parse_findings(raw, "sample.py", "return a / b", 1)

        self.assertEqual(parsed, [])

    def test_malformed_json_retries_once_then_drops(self) -> None:
        backend = FakeBackend("not json", "still not json")
        parsed = review("return a / b", "sample.py", backend=backend)

        self.assertEqual(parsed, [])
        self.assertEqual(len(backend.calls), 2)

    def test_malformed_json_retry_can_succeed(self) -> None:
        backend = FakeBackend("not json", response(llm_finding()))
        parsed = review("return a / b", "sample.py", backend=backend)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(len(backend.calls), 2)


class OllamaBackendHostTests(unittest.TestCase):
    def test_configured_host_builds_dedicated_client(self) -> None:
        backend = OllamaBackend(model="m", host="http://192.168.1.5:11434")
        self.assertIsInstance(backend._client, ollama.Client)

    def test_no_host_uses_module_client(self) -> None:
        # host=None defers to the module-level client, which honors OLLAMA_HOST.
        backend = OllamaBackend(model="m")
        self.assertIs(backend._client, ollama)


if __name__ == "__main__":
    unittest.main()
