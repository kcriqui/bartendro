# Demo bot with simulated pumps (fully working web app, no hardware), e.g. for TrueNAS +
# Tailscale Funnel - see docs/hosting.md. Starts from a fresh copy of deploy/demo-bot.db (Kevin's
# bot database) on every start, so whatever visitors change is undone by a restart.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/demo-bot.db ./
COPY deploy/uploads ./uploads
RUN pip install . && useradd --system --home /data bartendro && mkdir -p /data && chown bartendro /data
USER bartendro

ENV BANNER="Demo bot with simulated pumps - nothing really pours. Changes are undone when it restarts."
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)"
CMD ["sh", "-c", "exec bartendro-web --sim 15 --start-from /app/demo-bot.db --db /data/demo.db --host 0.0.0.0 --port 8080 --no-uploads --banner \"$BANNER\""]
