# Piparo upstream synchronization audit

This file records the semantic audit performed while merging upstream PR-Agent `0.39.0` into the Piparo fork.

## Synchronization boundary

- Previous common base: `751af2422bef9be99fa605734c058c1c9bc1b3fa`
- Previous Piparo head: `0422dbc139fa5b61b940d0b67f6d2ccbf80cc92a`
- Upstream head merged: `1885eb4056887b8c8a530f0a35b842bba05cb425`
- Merge commit: `8460c3a425c7f8588bac076b830120ecb8e7165b`
- Fork-only commits audited: 29

The merge uses upstream implementations as the base. Piparo behavior remains only where it is still required or provides functionality not present upstream. Old image-patching machinery was replaced by direct source builds.

## Fork-commit matrix

Classifications:

- **Retained**: the behavior remains directly in current source/configuration.
- **Retained, evolved**: the intent remains, but later Piparo or upstream work replaced the original implementation.
- **Superseded**: an intermediate implementation or fix no longer exists separately because a later implementation covers it.

| Commit | Classification | Current evidence |
| --- | --- | --- |
| `620a95a2` Add Piparo PR-Agent image | Retained, evolved | Root `Dockerfile` builds current source; `.github/workflows/build.yml` builds and publishes it. The former patch script is intentionally gone. |
| `841a6b15` Add dated PR-Agent deploy workflow | Retained, evolved | `.github/workflows/build.yml` creates immutable `YYYY-MM-DD-<short-sha>` tags and updates deployment manifests. |
| `682fc7b3` Fix empty PR suggestions comment | Retained, evolved | Empty results are handled directly in `pr_agent/tools/pr_code_suggestions.py`; covered by `tests/unittest/test_pr_code_suggestions_rendering.py`. |
| `7fe9ceba` Mark PR-Agent describe output | Retained | Generated description content uses visible notice plus `piparo-pr-agent:generated-start/end` markers in `pr_agent/tools/pr_description.py`. |
| `56c7eab7` Show PR-Agent rerun timestamps | Retained | Review and suggestion outputs append `Last review update` / `Last suggestions update` timestamps. |
| `0db395a1` Track upstream PR-Agent source directly | Retained, evolved | Upstream is a Git remote and ancestry of current `main`; the image copies repository source instead of patching an upstream image. |
| `0867cb0e` Clarify PR-Agent freeform prompt hint | Retained | `PIPARO_COMMAND_HINT` in `pr_agent/tools/pr_reviewer.py` documents free-form `@piparo-agent` instructions. |
| `e29cf952` Require self-review for code suggestions | Retained | `.pr_agent.toml` enables `demand_code_suggestions_self_review`; rendering remains in `pr_code_suggestions.py`. |
| `f46513b5` Add AI usage reporting | Retained, evolved | `pr_agent/algo/ai_usage.py` reports model/token usage and is covered by `tests/unittest/test_ai_usage.py`. |
| `290cae90` Fix deploy workflow | Superseded | The current single build/deploy workflow replaces the intermediate repair. |
| `d6735cc6` Build deploy image without Docker actions | Retained | The workflow invokes Docker CLI directly and does not depend on Docker build actions. |
| `ae7e3d0b` Place generated notice after user description | Retained | `tests/unittest/test_pr_description.py` asserts the user description precedes the generated marker/content. |
| `3615a8bf` Reduce follow-up PR-Agent review noise | Retained | `.pr_agent.toml` limits follow-up review/suggestion instructions to newly introduced changes and uses incremental review on pushes. |
| `02c67523` Use supported PR review command | Retained | Automatic commands use `/review`, with `/review -i` on pushes. |
| `c254beec` Isolate PR-Agent progress comments | Retained | Marker-specific progress comments remain; covered by `tests/unittest/test_piparo_progress_comments.py`. |
| `bab5c4b7` Run unit tests before deploy | Retained | `build-and-push` has `needs: test`; the test job runs `PYTHONPATH=. pytest tests/unittest -q`. |
| `af6232c7` Reduce production prompt logging | Retained | Default `config.log_level` is `INFO`; production manifests do not override it to debug. |
| `ad27a9f4` Wrap generated PR description content | Retained | Start/end markers wrap generated content; covered by `tests/unittest/test_pr_description.py`. |
| `81c6b44b` Slim runtime dependencies | Retained, evolved | Root `Dockerfile` installs only `requirements.txt`; development requirements remain outside the runtime image. Upstream-required runtime packages were retained. |
| `4c630024` Bound AI usage history | Retained | `config.ai_usage_total_max_runs` bounds detailed history while preserving totals; covered by `test_ai_usage.py`. |
| `a293cfd3` Remove unused PR-Agent config | Retained | Obsolete `review_agent` / auto-approval overrides remain absent from `.pr_agent.toml`. |
| `e8034ff1` Add Redis-backed PR queue | Retained | `pr_agent/servers/pr_processing_queue.py`, queue settings, GitHub startup/shutdown integration, and queue regression tests remain. |
| `f5110fe4` Run PR-Agent work off server event loop | Retained | `pr_agent/servers/async_utils.py` and webhook integrations execute blocking agent work in threads; queue reliability fixes remain. |
| `f15979c9` Publish PR-Agent progress statuses | Retained | `pr_agent/tools/progress_status.py` and provider integrations publish progress/final states; covered by progress-status tests. |
| `89f44700` Report cumulative PR-Agent usage as a check run | Retained | GitHub check-run implementation remains in `github_provider.py`; non-GitHub providers use a persistent fallback comment. |
| `001f190f` Add cost/latency and queue reliability fixes | Retained | AI usage includes estimated cost/duration; queue locking/expiry behavior and signature utility regressions are covered by unit tests. |
| `decaeb42` Add memory profiling and worker recycle | Retained | `pr_agent/servers/memory_profiler.py` and queued-worker recycle controls remain integrated and tested. |
| `aaf35185` Load centralized repository settings | Retained | Global settings loading/caching remains in `github_provider.py` and `git_providers/utils.py`; covered by repository-settings tests. |
| `0422dbc1` Pack global skill rules by profile | Retained | GitHub global settings resolve, clip, and pack generated profile rules; covered by `tests/unittest/test_repo_settings.py`. |

## Forgejo additions after synchronization

- `74abb7c5`: separate public/browser and internal API URLs; parse browser/API URLs; trust the PR head; read repository settings from the trusted base ref.
- `94e50915`: fail-closed signatures, Forgejo headers, owner allowlist, bot-loop suppression, health endpoints, and whitespace-tolerant commands.
- `e912f410`: update GitHub and Forgejo deployment image references atomically.
- `b45cafa2`: require a non-empty Forgejo owner allowlist, failing closed when it is missing.

Infrastructure is maintained separately in `piparotech/infra` under `pr-agent/forgejo.yaml` and `forgejo/forgejo.yaml`.

## Verification evidence

On the synchronized source plus Forgejo changes:

- `git fetch upstream main` still resolved to `1885eb4056887b8c8a530f0a35b842bba05cb425`; it is an ancestor of current `main`.
- No unresolved index entries; `git diff --check` and `git fsck --no-dangling` succeeded.
- Focused fork/Forgejo regression set: 107 passed.
- Full suite after final fail-closed hardening: `PYTHONPATH=. ./.venv/bin/pytest tests/unittest -q` → 1,408 passed, 1 skipped, 1 expected xfail.
- `linux/amd64` production image built from final source and both GitHub and Forgejo Gunicorn entry points returned healthy responses as UID 1000 with a read-only root filesystem.
- `kubectl kustomize` rendered both `infra/pr-agent` and `infra/forgejo`; the PR-Agent bundle, including the ExternalSecret CRD resources, passed Kubernetes API-server dry-run.
- Live Forgejo 16.0.1 provider validation read PR `pr-agent-smoke/pr-agent-smoke#1`, loaded its diff, and completed a comment create/edit/delete round trip.
- A signed live webhook against the production image invoked the configured model backend, produced a `piparo-agent` response on the isolated smoke PR, and removed all test comments afterward.

No commits were pushed and no pull requests were published during this synchronization.
