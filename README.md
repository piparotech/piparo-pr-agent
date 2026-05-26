# piparo-pr-agent

Piparo custom image for [PR-Agent](https://github.com/qodo-ai/pr-agent).

Changes on top of the pinned upstream image:

- Adds friendly `/improve` summary text.
- Shows low-impact suggestions in one summary comment.
- Keeps high-impact suggestions eligible for inline publishing via PR-Agent config.
- Publishes an immediate "in progress" comment for `/review` and `/improve`, then updates that same comment with the final content.
- Adds a short command hint to the review comment.

The image is published to:

```text
ghcr.io/piparotech/piparo-pr-agent
```
