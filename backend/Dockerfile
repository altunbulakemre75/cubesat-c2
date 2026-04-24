FROM python:3.11-slim
WORKDIR /app

# curl needed for Docker health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN mkdir -p src && pip install --no-cache-dir -e ".[dev]"

COPY . .

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
