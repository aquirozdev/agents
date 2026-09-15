FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "httpx>=0.27,<1" \
    "pydantic>=2.8,<3" \
    "pydantic-settings>=2.4,<3" \
    "PyYAML>=6.0,<7" \
    "uvicorn[standard]>=0.30,<1"

RUN addgroup --system banking && adduser --system --ingroup banking banking \
    && mkdir -p /data \
    && chown -R banking:banking /app /data

USER banking

EXPOSE 8080
CMD ["uvicorn", "banking_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
