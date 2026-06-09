# prr — a local code-review cat

`prr` (as in *purr*) is a self-hosted code-review bot with a cat for a mascot. A local model watches your PRs, leaves **inline comments on the offending lines** with **one-click fix suggestions**, and scans whole repos on demand. Same idea as Codex review / CodeRabbit / Bugbot — but the brain runs on your own GPU and nothing leaves the box. It purrs when your PR is clean and swats when it finds a bug.

> Personal project. Not a product. Built so the **model is a swappable part** — revisit later, drop in a better open model, watch it improve for free.

## Platforms & setup

Supported: **macOS**, **native Linux**, and **WSL2**. The code is pure Python (`pathlib`,
UTF-8 everywhere, no shell-outs beyond arg-list `subprocess`), so the same steps work on all three.

**macOS / native Linux**
```bash
# 1. install uv (https://docs.astral.sh/uv/)
# 2. start Ollama and pull the model
ollama pull qwen2.5-coder:14b
# 3. run
uv run prr review sample.py
```

**WSL2** — same as Linux, with one wrinkle: if Ollama runs on the **Windows host** (where the GPU
usually is) or on a remote box, `localhost:11434` may not reach it from inside WSL2 under default
NAT networking. Two options:
- Point `prr` at the host explicitly — set `ollama_host` in `config.yaml`
  (e.g. `ollama_host: http://<windows-host-ip>:11434`) or export `OLLAMA_HOST=http://<host>:11434`.
- Or enable WSL2 **mirrored networking** (Windows 11), which forwards `localhost` to the host so no
  host config is needed.

`prr` resolves the Ollama host in this order: `config.yaml` `ollama_host` → `OLLAMA_HOST` env →
the client default (`http://localhost:11434`).

**Windows** — use **WSL2**. Native Windows is not separately supported: the GPU/self-hosted-runner/
Ollama/static-tool stack is most uniform on Linux, and WSL2 gives Windows users a real Linux
environment on the same machine without a second codebase to maintain.

CI runs the test + lint suite on `ubuntu-latest` (covers Linux/WSL2) and `macos-latest`.

## Locked decisions

- **Hook into GitHub:** self-hosted GitHub Actions runner (+ a `prr` CLI). The runner sits on the GPU machine, so it reaches the local model directly and is **outbound-only** — no inbound webhook, no exposing your home network. The CLI shares the same core for whole-repo scans and local testing.
- **Find issues:** hybrid, LLM-led. Deterministic tools (`ruff`, `mypy`, `bandit`) find precise, located facts; the LLM reasons about bugs/edge cases and writes the fix. Anchoring on real tool findings is what keeps a weaker local model trustworthy.
- **Scope:** both PR-diff review (Action-triggered) and whole-repo scan (CLI).

## Mascot & voice

prr reacts to your code. Moods are a presentation layer rendered from `severity` — the logic stays technical, the surface is a cat:

| mood | severity | meaning |
|------|----------|---------|
| purr | (none) | clean — LGTM, nothing to flag |
| chirp | info | `nit:` — minor, optional |
| hiss | warning | a real smell worth a look |
| swat | error | bug/security — fix this |

The PR summary comment is the cat's verdict (`prr is purring` vs `prr is not happy`) plus counts by severity. A clean CLI run prints a contented cat.

## Architecture — one model-agnostic core, two thin front-ends

```
input adapter  -> deterministic pass -> context builder -> [MODEL SEAM] -> filter -> output adapter
(PR hunks or     (ruff/mypy/bandit     (code + neighbors   review(ctx)     validate   PR review / report
 repo chunks)     -> located facts)     + tool findings)   -> Finding[]    lines,
                                                                            dedup,
                                                                            threshold,
                                                                            cap
```

The **core** is the durable asset. The **model seam** is the only thing you replace on a future revisit.

### Repo layout
```
prr/
  core/
    schema.py         # Pydantic Finding model (defined once, used everywhere)
    ingest.py         # tree-sitter chunking + PR diff parsing
    detect_static.py  # ruff / mypy / bandit runners -> Finding[]
    context.py        # build per-target context (code + neighbors + tool findings)
    model.py          # THE SEAM: review(context) -> list[Finding]
    filter.py         # validate lines, dedup, threshold, cap
    github_out.py     # build + post GitHub review (inline + suggestion blocks)
    prompts/
  frontends/
    cli.py            # prr review <file|--pr> / prr scan <path>
    action_entry.py   # called by the workflow
  config.yaml         # model, ollama_host, severity threshold, ignore paths, max comments
  .github/workflows/review.yml
```

### The Finding schema (the contract everything speaks)
```python
class Finding(BaseModel):
    path: str
    line: int
    end_line: int | None = None
    severity: Literal["info", "warning", "error"]   # cat mood is rendered from this
    category: Literal["bug", "security", "style", "perf", "test", "other"]
    comment: str                 # terse human explanation
    suggestion: str | None = None  # replacement code for the line(s)
    source: Literal["llm", "ruff", "mypy", "bandit"]
    confidence: float = 1.0      # used for thresholding LLM findings
```

## Default stack (all swappable)
- CLI: `prr`.
- Bot language: Python.
- Model: `qwen2.5-coder:14b` via **Ollama** to start; move the seam to Qwen3-Coder/Devstral or **vLLM** later for guided decoding + concurrency.
- Structured output: Ollama schema-guided `format` returning `{"findings": [...]}` / vLLM guided decoding + Pydantic validation.
- GitHub: REST review API; self-hosted runner; workflow `GITHUB_TOKEN`.

## The weeks
1. `week-1-core-and-model-seam.md` — structured review of one file from the CLI.
2. `week-2-deterministic-pass-and-filter.md` — trustworthy whole-repo scan report.
3. `week-3-pr-mode-and-github-output.md` — real inline review on a real PR (manual trigger).
4. `week-4-runner-and-automation.md` — auto-review on PR events, fully local.
5. `week-5-polish-and-handoff.md` — leave it running; clean for future model swaps.

Roughly 4–5 weeks of real work. Week 5 is optional polish.

## Global gotchas (read before starting)
- **Phantom lines** are the #1 review-bot failure. The filter must drop any finding whose line doesn't exist in the file/diff, and the prompt must force the model to quote the exact line.
- **Noise.** Local models nitpick. Severity threshold + dedup + a hard per-PR cap + "only if confident" prompting. A 40-comment cat gets muted.
- **Runner security.** Private/trusted repos only — a self-hosted runner runs untrusted public-fork code on your machine.
- **Structured-output breakage.** Use JSON/guided decoding + a parse-retry.

## Future revisit (the whole point)
Change the model in `config.yaml` → run `prr eval` (the seeded known-bug check from Week 5) → done. No rewrite.
