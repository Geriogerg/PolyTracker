FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POLYTRACKER_CONFIG=/app/config.yaml

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY polytracker ./polytracker
COPY tests ./tests
COPY config.default.yaml /app/config.yaml

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)" || exit 1
CMD ["python", "-m", "polytracker", "api"]
