FROM dhi.io/python:3.12-alpine3.21-dev AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM dhi.io/python:3.12-alpine3.21-dev
RUN apk add --no-cache ffmpeg
WORKDIR /app
COPY --from=builder /opt/python/lib/python3.12/site-packages /opt/python/lib/python3.12/site-packages
COPY --from=builder /opt/python/bin /opt/python/bin
COPY . .
RUN adduser -D -s /bin/sh appuser && \
    mkdir -p /tmp/session-digest && \
    chown -R appuser:appuser /app /tmp/session-digest
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
