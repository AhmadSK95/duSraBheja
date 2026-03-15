FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy source code
COPY . .

# Install dependencies after the project tree is available.
RUN uv pip install --system -e ".[dev]"

# Default command (overridden per service in docker-compose)
CMD ["python", "-m", "src.bot.main"]
