FROM python:3.12.13-slim

RUN apt-get update \
  && apt-get install --no-install-recommends -y git curl \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt \
  && rm requirements.txt

COPY docs docs
COPY pr_agent pr_agent

ENV PYTHONPATH=/app

EXPOSE 3000
CMD ["python", "-m", "gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-c", "pr_agent/servers/gunicorn_config.py", "--forwarded-allow-ips", "*", "pr_agent.servers.github_app:app"]
