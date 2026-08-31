FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Xvfb gives Chromium a real X display so it can run headed; x11vnc + noVNC
# expose that display over HTTP for the one-time QR scan login.
# fonts-noto-cjk: without it the Doubao pages render as tofu boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify supervisor fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY docker/ /docker/
RUN chmod +x /docker/x11vnc.sh

COPY doubao2api/ doubao2api/

ENV DISPLAY=:99 \
    DOUBAO_HOST=0.0.0.0 \
    DOUBAO_PORT=9090 \
    DOUBAO_HEADLESS=false \
    DOUBAO_BROWSER_DATA=/data/browser \
    DOUBAO_ACCOUNTS_FILE=/data/accounts.json

VOLUME ["/data"]

EXPOSE 9090 6080

CMD ["supervisord", "-c", "/docker/supervisord.conf"]
