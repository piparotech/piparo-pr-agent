FROM python:3.12.13-slim

RUN apt-get update \
  && apt-get install --no-install-recommends -y git curl \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY docs docs
RUN pip install --no-cache-dir . \
  && rm pyproject.toml requirements.txt

ENV PYTHONPATH=/app

COPY pr_agent pr_agent

EXPOSE 3000
CMD ["python", "-m", "gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-c", "pr_agent/servers/gunicorn_config.py", "--forwarded-allow-ips", "*", "pr_agent.servers.github_app:app"]
