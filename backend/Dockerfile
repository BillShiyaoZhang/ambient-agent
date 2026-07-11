FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Copy only the necessary project configurations first to leverage layer caching
COPY pyproject.toml README.md /app/

# Install dependencies (only production packages)
RUN uv pip install --system .

# Copy backend codebase
COPY backend /app/backend/
COPY forward /app/forward/
COPY scripts /app/scripts/

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
