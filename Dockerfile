# Pixelle-Video Docker Image
# Based on Python 3.11 slim for smaller image size

FROM python:3.11-slim

# Build arguments for mirror configuration
# USE_CN_MIRROR: whether to use China mirrors (true/false)
ARG USE_CN_MIRROR=false

# Set working directory
WORKDIR /app

# Replace apt sources with China mirrors if needed
# Debian 12 uses DEB822 format in /etc/apt/sources.list.d/debian.sources
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi

# Install system dependencies
# - curl: for health checks and downloads
# - ffmpeg: for video/audio processing
# - fonts-noto-cjk: for CJK character support
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager (pinned version)
ARG UV_VERSION=0.11.7
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ "uv==${UV_VERSION}"; \
    else \
        pip install --no-cache-dir "uv==${UV_VERSION}"; \
    fi
ENV PATH="/root/.local/bin:$PATH"
RUN uv --version

# Copy dependency files and source code for building
# Note: pixelle_video and orchestrator are the wheel packages listed in
# pyproject.toml; api/app.py imports orchestrator at start-up.
COPY pyproject.toml uv.lock README.md ./
COPY pixelle_video ./pixelle_video
COPY orchestrator ./orchestrator

# Create virtual environment and install the versions pinned in uv.lock, the
# same lockfile CI installs from (`uv sync --locked`). Only the runtime
# dependencies are installed; the "dev" extra (pytest, ruff) is left out.
# `uv sync --frozen` downloads from the URLs recorded in uv.lock and ignores
# index options, so with USE_CN_MIRROR=true the locked versions are exported
# to a requirements file (with hashes) and installed from the mirror instead.
RUN export UV_HTTP_TIMEOUT=300 && \
    uv venv && \
    if [ "$USE_CN_MIRROR" = "true" ]; then \
        uv export --frozen --no-dev --no-emit-project --no-header \
            --output-file /tmp/requirements.lock.txt && \
        uv pip install -r /tmp/requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple && \
        uv pip install --no-deps -e . -i https://pypi.tuna.tsinghua.edu.cn/simple && \
        rm /tmp/requirements.lock.txt; \
    else \
        uv sync --frozen --no-dev; \
    fi && \
    .venv/bin/playwright install --with-deps chromium

# Copy rest of application code
COPY api ./api
COPY web ./web
COPY bgm ./bgm
COPY templates ./templates
COPY workflows ./workflows
COPY resources ./resources
COPY docs/images ./docs/images
COPY docs/FAQ*.md ./docs/

# Create output, data and temp directories
RUN mkdir -p /app/output /app/data /app/temp

# Expose ports
# 8000: API service
# 8501: Web UI service
EXPOSE 8000 8501

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "python", "api/app.py", "--host", "0.0.0.0"]

