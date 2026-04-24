FROM python:3.11-slim
WORKDIR /app

# Install deps first (layer cache: only rebuild on pyproject.toml change)
COPY pyproject.toml .
# Create minimal src/ so hatchling can find the package
RUN mkdir -p src && pip install --no-cache-dir -e ".[dev]"

# Copy full source (this layer rebuilds on any source change)
COPY . .

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
