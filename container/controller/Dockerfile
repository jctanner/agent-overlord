FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl nodejs npm \
    && npm install -g @anthropic-ai/claude-code @openai/codex \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash agent \
    && mkdir -p /home/agent/.config/gcloud /home/agent/.codex /home/agent/scratch \
    && chown -R agent:agent /home/agent

ENV HOME=/home/agent \
    CODEX_HOME=/home/agent/.codex \
    GOOGLE_APPLICATION_CREDENTIALS=/home/agent/.config/gcloud/application_default_credentials.json

USER agent
WORKDIR /home/agent/scratch
CMD ["sleep", "infinity"]
