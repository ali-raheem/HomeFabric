# Optional container distribution. Native installs are the primary path.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY homefabric ./homefabric
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home /data homefabric && mkdir -p /data && chown homefabric /data
COPY --from=build /app/.venv /app/.venv
COPY manage.py /app/
ENV PATH="/app/.venv/bin:$PATH" \
    FABRIC_DATA_DIR=/data \
    FABRIC_BIND=0.0.0.0:8000 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER homefabric
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["sh", "-c", "homefabric migrate --no-input && homefabric serve"]
