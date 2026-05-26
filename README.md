# piparo-pr-agent

Piparo-maintained PR-Agent fork based on [`The-PR-Agent/pr-agent`](https://github.com/The-PR-Agent/pr-agent).

This repository keeps the existing `piparotech/piparo-pr-agent` URL, but tracks upstream via an `upstream` git remote instead of using GitHub's fork relationship.

## Piparo changes

- Friendly `/improve` summary text.
- Low-impact suggestions collected into one summary comment.
- High-impact suggestions can still be published inline through PR-Agent config.
- Persistent `/review` and `/improve` progress comments that are updated with final output.
- Review comments include a short `@piparo-agent` command hint.
- `/describe` output is wrapped in visible Piparo generated-content markers.
- The image is built from this repository's source instead of patching files inside an upstream image.

## Image

The GitHub Actions workflow publishes unique deploy images:

```text
ghcr.io/piparotech/piparo-pr-agent:YYYY-MM-DD-<short-sha>
```

It then updates `piparotech/infra` at `pr-agent/pr-agent.yaml` to the same unique tag. The workflow needs `DEPLOY_PAT` with write access to `piparotech/infra`.

## Sync upstream

```bash
git remote add upstream git@github.com:The-PR-Agent/pr-agent.git
git remote set-url --push upstream DISABLED
git fetch upstream main --tags
git checkout main
git merge upstream/main
# resolve conflicts, run tests/build, then commit and push
```
