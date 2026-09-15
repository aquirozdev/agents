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
    "pydantic-settings>=2.4,<3" \
    "PyJWT[crypto]>=2.8,<3" \
    "uvicorn[standard]>=0.30,<1"

RUN addgroup --system identity && adduser --system --ingroup identity identity
USER identity

EXPOSE 8081
CMD ["uvicorn", "identity_proxy.main:app", "--host", "0.0.0.0", "--port", "8081"]
