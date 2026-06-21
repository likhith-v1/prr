# prr

A local Python code-review CLI that pairs static analysis with an Ollama model.
Review a file, scan a project, or post inline comments on a GitHub pull request —
all from your machine, with no cloud API keys required for the review brain.

`prr` runs **ruff**, **mypy**, and **bandit**, feeds their findings into an LLM
chunk-by-chunk, validates everything against a shared schema, and renders
cat-themed output in the terminal or on GitHub.

## Features

- **Single-file review** — `prr review <file>`
- **Project scan** — `prr scan <path>` with configurable ignore globs
- **GitHub PR reviews** — `prr review --pr owner/repo#n` posts one batched inline
  review with optional one-click suggestions
- **Seeded eval** — `prr eval` regression checks when swapping models
- **GitHub Actions** — optional self-hosted workflow for automatic PR review
- **Fail-closed output** — unparseable or unanchored model findings are dropped,
  not trusted blindly

## Requirements

- macOS, native Linux, or WSL2 (native Windows is not supported — use WSL2)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) with the configured model pulled locally

Static tools (`ruff`, `mypy`, `bandit`) are installed as Python dependencies and
are available through `uv run`; you do not need separate system installs.

## Quick start

Clone the repo, install dependencies, and pull the default model:

```bash
git clone https://github.com/likhith-v1/prr.git
cd prr
ollama pull qwen2.5-coder:14b
uv sync --extra test
```

Try the bundled sample file:

```bash
uv run prr review sample.py
```

Scan the project:

```bash
uv run prr scan .
```

## Usage

### Review one file

```bash
uv run prr review path/to/file.py
```

### Scan a file or directory

```bash
uv run prr scan .
uv run prr scan src/
```

Ignored paths come from `config.yaml` (defaults include `.venv/`, `__pycache__/`,
and other build/cache directories).

### Review a GitHub pull request

Fetches changed Python files at the PR head, reviews only added lines, and posts
**one** batched review with inline comments and a summary.

```bash
export GITHUB_TOKEN=...   # PAT with pull-request access, or use `gh auth setup-git`
uv run prr review --pr owner/repo#123 --dry-run   # preview without posting
uv run prr review --pr owner/repo#123             # post the review
```

With [GitHub CLI](https://cli.github.com/) authenticated, you can populate `GITHUB_TOKEN` from it:

```bash
gh auth login
export GITHUB_TOKEN="$(gh auth token)"
uv run prr review --pr owner/repo#123
```

### Run the seeded eval

Use this before and after changing models in `config.yaml`:

```bash
uv run prr eval
```

## Configuration

`prr` reads `config.yaml` from the current working directory. Pass `--config` to
override:

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

| Setting | Purpose |
|---------|---------|
| `model` | Model name (Ollama tag or vLLM model ID) |
| `ollama_host` | Ollama server URL (see below) |
| `backend` | `ollama` (default) or `vllm` |
| `vllm_base_url` | vLLM server URL, e.g. `http://localhost:8000/v1` |
| `severity_threshold` | Drop findings below this severity |
| `min_confidence` | Drop LLM findings below this confidence |
| `max_comments_per_file` | Cap findings per file after filtering |
| `max_comments_per_pr` | Cap inline comments posted per PR review |
| `ignore_paths` | Glob patterns skipped by `prr scan` |

**Ollama host resolution** (first match wins):

1. `config.yaml` → `ollama_host`
2. `OLLAMA_HOST` environment variable
3. Ollama client default (`http://localhost:11434`)

On WSL2, set `ollama_host` when Ollama runs on the Windows host or a remote GPU
machine.

### WSL2 and Ollama on Windows

On WSL2 2.3+, `http://127.0.0.1:11434` usually works without any extra
configuration — recent WSL2 forwards localhost automatically to the Windows host.
Verify before running `prr`:

```bash
curl http://127.0.0.1:11434/api/tags
```

If that fails, find the Windows host IP and set `ollama_host` (see the
[`ollama_host` config row](#configuration) above):

```bash
# Most reliable on WSL2: read the nameserver entry
grep nameserver /etc/resolv.conf | awk '{print $2}'
```

```yaml
# config.yaml
ollama_host: http://192.168.x.x:11434
```

Or set it as an environment variable instead:

```bash
export OLLAMA_HOST=http://192.168.x.x:11434
```

**Windows side checklist:**

- Ollama is running (tray icon or `ollama serve`).
- "Expose Ollama to the network" is enabled in Ollama settings.
- Windows Firewall allows inbound TCP **11434** on the Private profile.
- The model is pulled: `ollama pull qwen2.5-coder:14b`.

**Optional — mirrored networking** (makes localhost more reliable across WSL
restarts). In `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then restart WSL: `wsl --shutdown`, reopen the terminal.

## How it works

```text
input files
  → tree-sitter chunks (functions, methods, classes, module-level code)
  → ruff / mypy / bandit
  → context + prior static findings attached per chunk
  → Ollama review backend
  → Finding validation and filtering
  → CLI or GitHub output
```

Every producer and consumer speaks one schema — `core.schema.Finding`:

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

**Model output** must be a JSON object: `{"findings": [...]}`. Invalid output
fails closed:

- unparseable JSON is dropped after one retry
- invalid findings are dropped
- findings whose `snippet` cannot be anchored to the source line are dropped
- findings outside the file's line range are dropped by the filter

When static tools and the LLM flag the same line, static tools keep the located
fact; the LLM can add explanation or a replacement `suggestion`.

Severity maps to cat moods in the CLI and on GitHub: error → 😾, warning → 🙀,
info → 😸.

## GitHub PR review details

`prr review --pr owner/repo#n`:

- posts inline comments on the **RIGHT** (new) side of the diff
- renders `suggestion` fields as one-click GitHub `suggestion` blocks
- includes a summary with severity counts and a cat verdict
- drops findings outside added lines
- notes patchless or skipped Python files in the summary
- runs file-scoped static analysis only (`ruff` and `bandit`; `mypy` is skipped
  until full-checkout review is supported)
- caps comments at `max_comments_per_pr`; overflow is noted in the summary

Use `--dry-run` to inspect the review without posting.

## GitHub Actions automation

`.github/workflows/review.yml` runs `prr` on `pull_request` opened, synchronize,
and reopened events. It targets a **self-hosted** runner labeled `self-hosted`
and `gpu` — install that runner on a machine that can reach Ollama.

The workflow:

- checks out the trusted **base** commit, then reviews the PR **head** SHA from
  the event payload
- uses the repository-scoped Actions `GITHUB_TOKEN` to post reviews
- stays **green** when `prr` finds code issues; it fails only on runtime errors
  (config, GitHub API, model backend, or review posting failures)
- runs only for same-repository PRs (fork PRs are skipped)

**Security note:** a self-hosted runner executes repository code on your machine.
Use trusted or private repositories, and treat the runner host as part of your
trust boundary.

## Eval and model swaps

`prr eval` runs the normal pipeline over small synthetic cases stored as package
fixtures (`.py.txt` files materialized in a temp workspace, so `prr scan .` never
reviews them).

The eval reports caught, missed, and false-positive findings. Only `warning`- and
`error`-severity findings outside the expected set count as false positives;
`info`-severity noise is tolerated.

**Eval exit codes:**

| Code | Meaning |
|------|---------|
| `0` | No misses or false positives |
| `1` | Regression detected |
| `2` | Config, case loading, model, or runtime failure |

**Model swap procedure:**

1. Change `model:` in `config.yaml`.
2. Run `uv run prr eval`.
3. Keep the new model only if eval results improve or hold steady.

## Development

```bash
uv run --extra test pytest
uv run ruff check core frontends tests
```

If the environment blocks the default uv cache:

```bash
uv --cache-dir .uv-cache run --extra test pytest
uv --cache-dir .uv-cache run ruff check core frontends tests
```

Tests use fake model backends — live Ollama is not required in CI. See
[`AGENTS.md`](AGENTS.md) for contributor conventions.

CI runs on `ubuntu-latest` and `macos-latest` for every push and pull request.

## License

MIT — see [`LICENSE`](LICENSE).
