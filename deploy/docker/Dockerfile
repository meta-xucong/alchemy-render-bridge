FROM python:3.12-slim

ARG RENDER_BRIDGE_BUILD_REVISION=unknown

WORKDIR /opt/render-bridge
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RENDER_BRIDGE_BUILD_REVISION=${RENDER_BRIDGE_BUILD_REVISION}

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir '.[runtime]'

USER 65532:65532

ENTRYPOINT ["python", "-m", "render_bridge.runtime", "--config", "/etc/alchemy/render-bridge/bridge.json"]
