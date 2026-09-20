FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1\
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY api/requirements.txt .
RUN pip install -r requirements.txt
COPY api/requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY api/app ./app
COPY common ./common
COPY api/alembic ./alembic
COPY api/alembic.ini .
COPY api/tests ./tests
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request,sys; (lambda r: sys.exit(0 if r.getcode()==200 else 1))(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4))"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]