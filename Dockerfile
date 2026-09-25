# Tiller: python:3.11-slim, non-root, read-only root filesystem except /state.
# Build:  docker build -t tiller .
# Run:    docker run --read-only --tmpfs /tmp -v tiller-state:/state --env-file secrets.env tiller run
# The env file carries AGENT_WALLET_SECRET / TILLER_*_API_KEY; never bake secrets into the image.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 tiller \
    && useradd --uid 10001 --gid tiller --home-dir /home/tiller --create-home --shell /usr/sbin/nologin tiller \
    && install -d -o tiller -g tiller -m 0700 /state

WORKDIR /app
COPY pyproject.toml requirements.lock README.md* ./
# --require-hashes once WP-F regenerates the lock with pip-compile --generate-hashes (plain pins until then)
RUN if grep -q -- "--hash=" requirements.lock; then pip install --require-hashes -r requirements.lock; else pip install -r requirements.lock; fi
COPY src ./src
COPY config ./config
COPY data ./data
RUN pip install --no-deps . && rm -rf /root/.cache

USER tiller
WORKDIR /app
VOLUME ["/state"]
ENV TILLER_CONFIG=/app/config/tiller.toml
ENTRYPOINT ["tiller"]
CMD ["run"]
