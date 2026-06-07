# Week 4 — Runner + Automation

**Goal:** open or update a PR → prr auto-reviews within ~a minute, running entirely on your machine, zero cloud, zero cost.

**Milestone:** a `pull_request` event triggers the workflow on your self-hosted runner, which calls the core and posts the review using the workflow token.

## Why this week
This is what turns the CLI tool into a bot. The self-hosted runner is the key trick: it lives on the GPU machine (so it reaches local Ollama at `localhost`) and polls GitHub **outbound**, so there's no inbound webhook and no exposing your home network.

## Tasks
- [ ] Install the self-hosted runner on the GPU PC (repo/org **Settings → Actions → Runners → New self-hosted runner**). Label it e.g. `self-hosted, gpu`. Run it as a service so it survives reboots.
- [ ] `frontends/action_entry.py` — thin wrapper: read PR number + repo from the event env, run the core, post the review via `github_out`. (Reuses everything from Weeks 1–3.)
- [ ] `.github/workflows/review.yml`:
  ```yaml
  name: prr
  on:
    pull_request:
      types: [opened, synchronize, reopened]
  permissions:
    pull-requests: write
    contents: read
  jobs:
    review:
      runs-on: [self-hosted, gpu]
      steps:
        - uses: actions/checkout@v4
        - run: python -m frontends.action_entry
          env:
            GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
            PR_NUMBER: ${{ github.event.pull_request.number }}
  ```
- [ ] Use the workflow `GITHUB_TOKEN` (not a PAT) to post — scoped to the repo, no personal secret in CI.
- [ ] Read `config.yaml` from the repo at runtime so each repo can tune thresholds/ignores.

## Deliverables
- Self-hosted runner installed and running as a service.
- The workflow + `action_entry.py` posting reviews automatically.
- Per-repo config honored.

## Done when
- Opening/pushing to a PR produces an automatic review within ~a minute.
- It runs offline against your local model, costing nothing.

## Notes / gotchas
- **Security — private/trusted repos only.** A self-hosted runner executes untrusted code from public-fork PRs on your machine. For public repos: require manual approval before workflows run, or block fork PRs. Don't skip this.
- The runner reaches Ollama at `localhost` directly — no networking needed between them.
- **Concurrency:** two PRs at once = two model calls. Serialize/queue requests, or move the seam to vLLM (batching) if it becomes a bottleneck.
