FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY planner/ planner/
COPY static/ static/

RUN useradd --create-home --uid 10001 planner
USER planner

EXPOSE 8080
CMD exec uvicorn planner.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}
