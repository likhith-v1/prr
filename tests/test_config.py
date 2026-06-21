from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.config import PrrConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_ollama_host_defaults_to_none(self) -> None:
        self.assertIsNone(PrrConfig().ollama_host)

    def test_ollama_host_round_trips_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "ollama_host: http://192.168.1.5:11434\n", encoding="utf-8"
            )
            config = load_config(path)

        self.assertEqual(config.ollama_host, "http://192.168.1.5:11434")

    def test_backend_defaults_to_ollama(self) -> None:
        self.assertEqual(PrrConfig().backend, "ollama")

    def test_vllm_base_url_defaults_to_none(self) -> None:
        self.assertIsNone(PrrConfig().vllm_base_url)

    def test_vllm_fields_round_trip_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "backend: vllm\nvllm_base_url: http://localhost:8000/v1\n",
                encoding="utf-8",
            )
            config = load_config(path)

        self.assertEqual(config.backend, "vllm")
        self.assertEqual(config.vllm_base_url, "http://localhost:8000/v1")


if __name__ == "__main__":
    unittest.main()
