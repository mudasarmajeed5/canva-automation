FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CANVA_CHROMIUM_NO_SANDBOX=1 \
    PORT=8473

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

RUN useradd -m -u 1000 app \
    && mkdir -p /app/downloads /app/data /app/chrome-profile /app/debug \
    && chown -R app:app /app /ms-playwright

USER app

EXPOSE 8473

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
