## Run a Gitea webhook server

1. In Gitea create a new user and give it "Reporter" role for the intended group or project.

2. For the user from step 1. generate a `personal_access_token` with `api` access.

3. Generate a random secret for your app, and save it for later (`webhook_secret`). For example, you can use:

    ```bash
    WEBHOOK_SECRET=$(python -c "import secrets; print(secrets.token_hex(10))")
    ```

4. Clone this repository:

    ```bash
    git clone https://github.com/the-pr-agent/pr-agent.git
    ```

5. Prepare variables and secrets. Skip this step if you plan on setting these as environment variables when running the agent:
    - In the configuration file/variables:
        - Set `config.git_provider` to "gitea"
    - In the secrets file/variables:
        - Set your AI model key in the respective section
        - In the [Gitea] section, set `personal_access_token` (with token from step 2) and `webhook_secret` (with secret from step 3)

6. Build a Docker image for the app and optionally push it to a Docker repository. We'll use Dockerhub as an example:

    ```bash
    docker build -f docker/Dockerfile -t pr-agent:gitea_app --target gitea_app .
    docker push pragent/pr-agent:gitea_webhook  # Push to your Docker repository
    ```

7. Set the environmental variables, the method depends on your docker runtime. Skip this step if you included your secrets/configuration directly in the Docker image.

    ```bash
    CONFIG__GIT_PROVIDER=gitea
    GITEA__PERSONAL_ACCESS_TOKEN=<personal_access_token>
    GITEA__WEBHOOK_SECRET=<webhook_secret>
    GITEA__URL=https://gitea.com # Public browser URL, or your Forgejo/Gitea host
    GITEA__API_URL=https://gitea.com # Optional internal API base when it differs from the browser URL
    GITEA__ALLOWED_OWNERS=approved-owner # Required; empty/missing fails closed
    GITEA__BOT_USER=pr-agent-bot # Required to suppress webhook loops from the bot itself
    OPENAI__KEY=<your_openai_api_key>
    GITEA__SKIP_SSL_VERIFICATION=false # or true
    GITEA__SSL_CA_CERT=/path/to/cacert.pem
    ```

8. Create a Forgejo/Gitea webhook. Set the URL to `http[s]://<PR_AGENT_HOSTNAME>/api/v1/gitea_webhooks`, the secret token to the generated secret from step 3, and enable `pull_request` and `issue_comment` events. Requests without a valid `X-Gitea-Signature` are rejected. The webhook target must also be allowed by the Forgejo/Gitea server's outbound webhook host policy.

9. Use a dedicated non-admin bot token with only the repository and issue permissions required for reviews and comments. Keep GitHub and Forgejo servers in separate processes because `CONFIG__GIT_PROVIDER` is process-global.

10. Test your installation by opening a pull request or commenting on one using a PR-Agent command.
