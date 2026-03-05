FROM dhi.io/python:3.12-alpine3.21-dev AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM dhi.io/python:3.12-alpine3.21-dev
RUN apk upgrade --no-cache && apk add --no-cache ffmpeg su-exec
WORKDIR /app
COPY --from=builder /opt/python/lib/python3.12/site-packages /opt/python/lib/python3.12/site-packages
COPY --from=builder /opt/python/bin /opt/python/bin
COPY . .
RUN adduser -D -s /bin/sh appuser && \
    mkdir -p /tmp/session-digest && \
    chown -R appuser:appuser /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
