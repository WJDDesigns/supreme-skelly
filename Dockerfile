# Supreme Skelly: headless controller + web UI.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SKELLY_WEB_DIR=/app/web \
    SKELLY_PORT=8420

WORKDIR /app
COPY pyproject.toml ./
COPY server ./server
RUN pip install --no-cache-dir .
COPY web ./web

# Runs as root so it can talk to the host's BlueZ over the mounted D-Bus socket.
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8420/api/health'); sys.exit(0)"
CMD ["supreme-skelly"]
