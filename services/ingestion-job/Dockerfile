FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ingestion/ ingestion/
COPY seed/ seed/
COPY fixtures/ fixtures/

RUN useradd --system --no-create-home --uid 10001 job
USER job

CMD ["python", "-m", "ingestion.main"]
