# redisearch-ci-common

Shared, reusable CI building blocks for the RediSearch family of repositories.

These pieces let each repo run a Codex agent with one consistent, hardened
security posture instead of every repo copy-pasting (and drifting on) the same
plumbing.

> **Keep everything here generic.** No secrets, and no logic specific to a
> single repository (branch names, prompt text, internal process). Callers pass
> any repo-specific values in as inputs and secrets.

## What's here

### Codex agent plumbing

| Unit | Path | Use it when… |
|---|---|---|
| `codex-run` composite action | [`.github/actions/codex-run`](.github/actions/codex-run/action.yml) | you need to run Codex as **a step** that can **modify** the tree (workspace-write), inside a job you already control. |
| `codex-ci-triage` composite action | [`.github/actions/codex-ci-triage`](.github/actions/codex-ci-triage/action.yml) | you need Codex to **analyze, read-only** (CI logs, reports) and hand back its summary as step outputs for a Slack/PR notification. Never fails the job. |
| `codex-agent` reusable workflow | [`.github/workflows/codex-agent.yml`](.github/workflows/codex-agent.yml) | you want a whole **job** that mints a scoped App token, checks out the caller repo, optionally runs a resolver, then runs Codex — driven by a label/comment trigger. |
| `common.py` helpers | [`scripts/ci_common/common.py`](scripts/ci_common/common.py) | your resolver script needs `gh` / `$GITHUB_OUTPUT` / context-file helpers. |

> **Triage / backport workflows themselves are per-repo, not here.** Each repo's
> triage and backport-agent workflows carry product-specific triggers, CI-report
> formats, and prompts, so they live in the consuming repo as thin callers of
> these runners (`codex-run` for write flows like backport, `codex-ci-triage` for
> read-only analysis). The shared, reusable substrate is what lives here.

### Shared CI utilities

| Unit | Path | Purpose |
|---|---|---|
| `slack-notify` composite action | [`.github/actions/slack-notify`](.github/actions/slack-notify/action.yml) | Post a payload to a Slack webhook — replaces every repo's hand-rolled "notify failure" step. Webhook is a caller secret. |
| `pr-size-label` reusable workflow | [`.github/workflows/pr-size-label.yml`](.github/workflows/pr-size-label.yml) | Label a PR by diff size. Add a thin `on: pull_request` caller. |
| `spellcheck` reusable workflow | [`.github/workflows/spellcheck.yml`](.github/workflows/spellcheck.yml) | codespell over a PR's changed files. The repo supplies its own `.codespell/` config. |
| `link-check` reusable workflow | [`.github/workflows/link-check.yml`](.github/workflows/link-check.yml) | Validate Markdown links/anchors. Self-contained — bundles `scripts/ci_common/check_links.py`. |

### Flaky-test DB

A shared, `REPO`/`BRANCH`-keyed Redis DB of flaky-test marks, so the family's
RLTest suites can quarantine known-flaky tests consistently.

| Unit | Path | Purpose |
|---|---|---|
| `flaky-mark` reusable workflow | [`.github/workflows/flaky-mark.yml`](.github/workflows/flaky-mark.yml) | Add a flaky mark (test id, reason, Jira key, expiry). Add a thin `on: workflow_dispatch` caller. |
| `flaky-unmark` reusable workflow | [`.github/workflows/flaky-unmark.yml`](.github/workflows/flaky-unmark.yml) | Remove a flaky mark. |
| `flaky_db.py` CLI | [`scripts/ci_common/flaky_db.py`](scripts/ci_common/flaky_db.py) | `mark`/`unmark`/`fetch`/`filter`/`record`. No-op when `REDIS_URL` is unset (keeps fork-PR CI green). |
| `flaky-record-results` composite action | [`.github/actions/flaky-record-results`](.github/actions/flaky-record-results/action.yml) | Record an RLTest run's failed/passed test ids to the DB from inside a test job (`if: always()`). Never fails the job. |

> Unlike the units above (which are test-framework agnostic), the flaky tooling
> is **RLTest-shaped**: it expects test ids in RLTest's
> `<test_file>:<test_name>[variant]` form — which is what the consuming suites
> emit. Marks only take effect once the consuming repo's **test pipeline** calls
> `flaky_db.py fetch`/`filter` to skip marked tests (and `flaky-record-results`
> to log per-run results); that wiring is product-specific and lives in each
> repo's own test workflow — intentionally **not** here.

### Security posture (baked into `codex-run`)

- `openai/codex-action` is **pinned to a commit SHA** (Sonar S7670).
- `sandbox: workspace-write` confines the agent to the checkout.
- Outbound network is granted only via the codex network override (for
  `git push` / `gh`) — far narrower than `danger-full-access`.
- `safety-strategy: drop-sudo` blocks privilege escalation on the runner.

## Using the composite action (step-level)

```yaml
- uses: actions/checkout@v6           # check out the repo to operate on
- name: Configure git
  run: |
    git config user.name  "redis-ci[bot]"
    git config user.email "redis-ci[bot]@users.noreply.github.com"
# ... your own steps: create a branch, attempt a merge, etc.
- name: Resolve via Codex
  uses: redislabsdev/redisearch-ci-common/.github/actions/codex-run@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    gh-token: ${{ steps.app-token.outputs.token }}
    prompt-file: .github/codex/prompts/your-prompt.md
```

The agent reads whatever context env vars you set in the job (e.g. a path to a
context JSON your resolver wrote). Keep that file in `$RUNNER_TEMP`, not the
working tree, so the agent's `git add -A` can't stage it.

## Using the reusable workflow (job-level)

```yaml
jobs:
  resolve:
    if: github.event.label.name == 'codex-resolve'
    uses: redislabsdev/redisearch-ci-common/.github/workflows/codex-agent.yml@v1
    with:
      app-id: ${{ vars.GH_CI_APP_ID }}
      prompt-file: .github/codex/prompts/your-prompt.md
      permission-contents: write
      fetch-depth: 0
    secrets:
      app-private-key: ${{ secrets.GH_CI_PRIVATE_KEY }}
      openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

## Versioning

Consumers should pin to an immutable ref — a tag (e.g. `@v1`) or a commit SHA —
rather than `@main`, so a change here can't silently alter every consumer's CI.
Tags are moved deliberately after the change has been validated in a consumer.
(The internal `codex-agent` → `codex-run` reference uses `@main` within this repo
and is updated together with each release.)

## License

Made available under your choice of the Redis Source Available License 2.0
(RSALv2), the Server Side Public License v1 (SSPLv1), or the GNU Affero General
Public License version 3 (AGPLv3) — matching the RediSearch project. See
[`LICENSE.txt`](LICENSE.txt) and the [`licenses/`](licenses/) folder.
