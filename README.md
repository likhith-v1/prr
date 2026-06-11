# prr

`prr` is a local Python code-review CLI backed by an Ollama model. It reviews
individual files, scans Python projects, combines deterministic tool findings
with LLM review, and filters output before showing it to the user.

Current status:
- Implemented: `prr review <file>`
- Implemented: `prr scan <path>`
- Implemented: `prr review --pr owner/repo#n` (GitHub PR inline review comments)
- Planned: self-hosted GitHub Actions runner integration

## Requirements

- macOS, native Linux, or WSL2
- Python 3.11+
- `uv`
- Ollama with the configured model available

Native Windows is not supported. Use WSL2 on Windows.

## Setup

Install dependencies and pull the default model:

```bash
ollama pull qwen2.5-coder:14b
uv sync --extra test
```

Review a single Python file:

```bash
uv run prr review sample.py
```

Scan a Python file or directory:

```bash
uv run prr scan .
```

Review a GitHub pull request (posts one batched inline review):

```bash
export GITHUB_TOKEN=...   # PAT with pull-request access
uv run prr review --pr owner/repo#123 --dry-run   # preview without posting
uv run prr review --pr owner/repo#123             # post the review
```

Run tests and lint:

```bash
uv run --extra test pytest
uv run ruff check core frontends tests
```

If the sandbox or environment blocks the default uv cache, use a local cache:

```bash
uv --cache-dir .uv-cache run --extra test pytest
uv --cache-dir .uv-cache run ruff check core frontends tests
```

## Configuration

`prr` reads `config.yaml` from the current working directory unless a command
accepts and receives `--config`.

```yaml
model: qwen2.5-coder:14b
# ollama_host: http://localhost:11434
severity_threshold: info
min_confidence: 0.7
max_comments_per_file: 20
max_comments_per_pr: 10
ignore_paths:
  - .git/**
  - .venv/**
  - .uv-cache/**
  - .pytest_cache/**
  - .ruff_cache/**
  - __pycache__/**
```

Ollama host resolution order:

1. `config.yaml` `ollama_host`
2. `OLLAMA_HOST`
3. Ollama client default, usually `http://localhost:11434`

For WSL2, set `ollama_host` when Ollama runs on the Windows host or on a remote
GPU machine.

## Architecture

The durable contract is `core.schema.Finding`. Model output, static-tool output,
filtering, CLI rendering, and future GitHub output should all preserve this
schema.

```python
class Finding(BaseModel):
    path: str
    line: int
    end_line: int | None = None
    severity: Literal["info", "warning", "error"]
    category: Literal["bug", "security", "style", "perf", "test", "other"]
    comment: str
    suggestion: str | None = None
    source: Literal["llm", "ruff", "mypy", "bandit"]
    confidence: float = 1.0
```

Review pipeline:

```text
input files
  -> tree-sitter chunks
  -> ruff / mypy / bandit
  -> context builder
  -> Ollama review backend
  -> Finding validation and filtering
  -> CLI output
```

Core modules:

- `core/schema.py`: shared `Finding` schema
- `core/ingest.py`: Python chunking with tree-sitter
- `core/detect_static.py`: `ruff`, `mypy`, and `bandit` adapters
- `core/context.py`: context and static-finding attachment
- `core/model.py`: Ollama model seam and structured output parsing
- `core/filter.py`: line validation, deduplication, thresholds, sorting, caps,
  diff-line restriction in PR mode
- `core/diff.py`: GitHub patch parsing into added/commentable line sets
- `core/github_out.py`: GitHub REST client and review payload builders
- `frontends/cli.py`: `review` (file or `--pr`) and `scan` commands

## Output Rules

Model output must be a JSON object:

```json
{"findings": []}
```

Invalid model output fails closed:

- unparseable JSON is dropped after one retry
- invalid findings are dropped
- findings whose snippets cannot be anchored to the source line are dropped
- findings outside the target file's line range are dropped by the filter

Static tool findings and LLM findings are merged when they land on the same
line. Static tools keep the located fact; LLM output can provide explanation or
replacement suggestions.

## GitHub PR Review

`prr review --pr owner/repo#n` fetches the PR's changed Python files at the
head commit, reviews only chunks that overlap added lines, and posts **one**
batched review:

- inline comments on the RIGHT side of the diff, prefixed with the cat mood
  for the finding's severity
- `suggestion` fields rendered as one-click ```suggestion blocks
- a summary body with the cat verdict and counts by severity
- findings outside added lines are dropped
- patchless/skipped Python files are noted in the summary
- PR static analysis is file-scoped; `mypy` is skipped until full-checkout
  review is added
- at most `max_comments_per_pr` comments per review; the rest are noted in
  the summary

Authentication uses a PAT from the `GITHUB_TOKEN` environment variable.
Use `--dry-run` to inspect the review without posting.

The intended deployment model is a self-hosted GitHub Actions runner on the
machine that can reach Ollama. Use trusted/private repositories only; a
self-hosted runner executes repository code on that machine.

## Development Roadmap

- Week 1: core schema, chunking, Ollama model seam, single-file review
- Week 2: deterministic static pass, context attachment, filtering, scan command
- Week 3: PR diff ingestion and GitHub review output
- Week 4: self-hosted runner automation
- Week 5: polish, evaluation, and model-swap handoff

## Notes

- Keep line numbers 1-based and absolute within the reviewed file.
- Use `pathlib` and UTF-8 file I/O.
- Use subprocess arg lists, not `shell=True`.
- Do not require live Ollama in automated tests.
- Keep the model backend swappable; callers should depend on the model seam, not
  on Ollama directly.
