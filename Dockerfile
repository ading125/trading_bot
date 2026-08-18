# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
RUN python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /build
COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
RUN pip install --requirement requirements.lock setuptools==83.0.0 \
    && pip install --no-build-isolation --no-deps .

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" investingbot \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --home-dir /nonexistent --shell /usr/sbin/nologin investingbot \
    && mkdir /data \
    && chown investingbot:investingbot /data

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INVESTING_BOT_BIND_HOST=0.0.0.0 \
    INVESTING_BOT_DATA_DIR=/data \
    INVESTING_BOT_ENVIRONMENT=production \
    INVESTING_BOT_LOG_FORMAT=json

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER investingbot:investingbot
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3)"]

ENTRYPOINT ["python", "-m", "investing_bot"]
