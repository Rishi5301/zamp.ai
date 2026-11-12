FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 PORT=8000 LEDGERFLOW_DATA=/var/lib/ledgerflow \
    XDG_CACHE_HOME=/tmp/ledgerflow-cache FORWARDED_ALLOW_IPS=*
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py db.py engine.py finance.py ai_extract.py fixtures.py container_entrypoint.py ./
COPY static ./static
COPY samples ./samples
RUN useradd --uid 10001 --create-home ledgerflow \
    && mkdir -p /var/lib/ledgerflow \
    && chown -R ledgerflow:ledgerflow /app /var/lib/ledgerflow
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/health',timeout=4)"
CMD ["python", "container_entrypoint.py"]
