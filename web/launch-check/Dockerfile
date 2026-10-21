# Launch Check web: Python stdlib app + Playwright headless Chromium shell for the 375 px phone render.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m playwright install --with-deps --only-shell chromium \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* /root/.cache

COPY . .
RUN python -m compileall -q . && useradd --create-home app && chown -R app /app
USER app

CMD ["python", "app.py"]
