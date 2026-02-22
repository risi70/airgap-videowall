FROM python:3.12-slim AS base
LABEL org.opencontainers.image.title="vw-config" \
      org.opencontainers.image.description="Videowall Configuration Authority" \
      org.opencontainers.image.licenses="EUPL-1.2"

RUN addgroup --system --gid 1000 vw && \
    adduser --system --uid 1000 --gid 1000 --no-create-home vw && \
    mkdir -p /var/lib/vw-config && chown vw:vw /var/lib/vw-config && \
    mkdir -p /etc/videowall

WORKDIR /app
COPY services/vw-config/pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi>=0.109" "uvicorn[standard]>=0.27" \
    "pyyaml>=6.0" "jsonschema>=4.21" "httpx>=0.27"
COPY services/vw-config/app/ app/
COPY config/schema.json /etc/videowall/schema.json

USER vw
EXPOSE 8006
ENV VW_CONFIG_PATH=/etc/videowall/platform-config.yaml \
    VW_CONFIG_POLL_INTERVAL=5 \
    VW_CONFIG_EVENT_LOG=/var/lib/vw-config/events.jsonl

HEALTHCHECK --interval=10s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8006/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006", "--log-level", "info"]
