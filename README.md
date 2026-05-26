# piparo-pr-agent

Piparo custom image for [PR-Agent](https://github.com/qodo-ai/pr-agent).

Changes on top of the pinned upstream image:

- Adds friendly `/improve` summary text.
- Shows low-impact suggestions in one summary comment.
- Keeps high-impact suggestions eligible for inline publishing via PR-Agent config.
- Publishes an immediate "in progress" comment for `/review` and `/improve`, then updates that same comment with the final content.
- Adds a short command hint to the review comment.
- Marks `/describe` output as a PR-Agent addition with a visible banner and hidden generated-content markers.

The image is published to GHCR with a UTC `YYYY-MM-DD` tag, for example:

```text
ghcr.io/piparotech/piparo-pr-agent:2026-05-26
```

The GitHub Actions workflow updates `piparotech/infra` so `pr-agent/pr-agent.yaml` uses the same dated tag. It requires a repository secret named `DEPLOY_PAT` with write access to `piparotech/infra`.
