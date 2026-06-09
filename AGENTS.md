# Agent Instructions

## Project Overview
- `prr` is a local Python code-review CLI backed by an Ollama model.
- The durable contract is `core.schema.Finding`; keep model, static-tool, filter, CLI, and future GitHub output behavior aligned to that schema.
- Current model output contract is a JSON object: `{"findings": [...]}`.

## Environment
- Use `uv` for dependency management and command execution.
- Preferred test command: `uv run --extra test pytest`.
- If the sandbox blocks uv cache writes, use a workspace-local cache: `uv --cache-dir .uv-cache run --extra test pytest`.
- Do not commit generated environments or caches: `.venv/`, `.uv-cache/`, `.pytest_cache/`, `.ruff_cache/`, and `__pycache__/`.

## Platforms
- Supported: macOS, native Linux, and WSL2. Windows users should use WSL2 — there is no separate native-Windows process.
- Keep the code portable: use `pathlib`, pass `encoding="utf-8"` on all file I/O, normalize paths with `.as_posix()` before glob/`fnmatch`, and call `subprocess` with arg-lists (no `shell=True`). Avoid `os.path`, `os.sep`, and `sys.platform`/`platform` branching.
- Line endings are normalized to LF via `.gitattributes`; chunking is CRLF-safe (line numbers stay aligned).
- Ollama host resolution order: `config.yaml` `ollama_host` → `OLLAMA_HOST` env → client default (`http://localhost:11434`). This lets WSL2 reach Ollama on a Windows/remote GPU host.
- CI (`.github/workflows/ci.yml`) runs tests + lint on `ubuntu-latest` (Linux/WSL2) and `macos-latest`; keep both green.

## Development Commands
- Run tests: `uv run --extra test pytest`.
- Run lint: `uv run ruff check core frontends tests`.
- Run the CLI: `uv run prr review sample.py`.
- The CLI requires Ollama to be running and the default model to be available: `ollama pull qwen2.5-coder:14b`.

## Implementation Rules
- Fail closed on model output. Drop unparseable, invalid, or unanchored findings instead of trusting model-reported lines.
- Keep line numbers 1-based and absolute within the reviewed file.
- Keep `snippet` anchoring strict: it must match the source line without the numeric prompt prefix.
- Prefer small, testable seams. Use fake backends in tests instead of requiring a live Ollama server.
- Preserve the object-wrapper JSON contract unless README, prompts, tests, and model parsing are updated together.

## Testing Expectations
- Add or update tests for schema changes, parser behavior, chunking, and CLI error paths.
- Do not rely on live Ollama in automated tests.
- A clean implementation should pass `uv run --extra test pytest` and `uv run ruff check core frontends tests`.
