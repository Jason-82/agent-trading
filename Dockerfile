# Tiller: python:3.11-slim, non-root, read-only root filesystem except /state.
# Build:  docker build -t tiller .
# Run:    docker run --read-only --tmpfs /tmp -v tiller-state:/state --env-file secrets.env tiller run
# The env file carries AGENT_WALLET_SECRET / TILLER_*_API_KEY; never bake secrets into the image
# (.dockerignore excludes config/tiller.toml, owner_overrides.json, config/secrets*, *.env, keys, state/).
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
COPY tools/check_lock.py ./tools/check_lock.py
# Supply chain (spec execution rule 10): the lock MUST carry hashes and pass the typosquat check;
# the build fails loudly otherwise. There is deliberately no unhashed fallback.
RUN python tools/check_lock.py requirements.lock \
    && pip install --require-hashes --no-deps -r requirements.lock
COPY src ./src
# only the example config files: the live config and overrides are mounted or written at runtime
COPY config/tiller.example.toml config/owner_overrides.example.json ./config/
COPY data ./data
RUN pip install --no-deps --no-build-isolation . && rm -rf /root/.cache

USER tiller
WORKDIR /app
VOLUME ["/state"]
ENV TILLER_CONFIG=/app/config/tiller.toml
ENTRYPOINT ["tiller"]
CMD ["run"]
