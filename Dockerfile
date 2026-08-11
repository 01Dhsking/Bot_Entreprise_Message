FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    PYTHONPATH=/app/src \
    HEADLESS=true \
    CHROME_PATH=/usr/bin/chromium \
    MCP_TRANSPORT=sse \
    MCP_PORT=8283 \
    BROWSER_DATA_DIR=/app/data/browser-profile

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/browser-profile \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8283

ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["python", "-m", "enterprise_message_bot.mcp_server"]
