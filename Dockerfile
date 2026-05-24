# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first for layer-cache efficiency
COPY pyproject.toml ./
# Copy lock file if it exists (optional — uv will regenerate if absent)
COPY uv.lock* ./

# Install production dependencies into /app/.venv
RUN uv sync --no-dev --frozen || uv sync --no-dev

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Create a non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY app/ ./app/

# Make sure the venv binaries take precedence
ENV PATH="/app/.venv/bin:$PATH"

# Drop privileges
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
