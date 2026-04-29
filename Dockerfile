# ── Stage 1: build the React frontend ───────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.12-slim

# System packages: git (Claude Code requirement), Node.js (Claude Code runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI via official installer
RUN curl -fsSL https://claude.ai/install.sh | bash

WORKDIR /app

# Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY backend/ ./backend/
COPY ksa_img_imm_to_text.py ./
COPY CLAUDE.md ./

# Built frontend (served by FastAPI at runtime)
COPY --from=frontend-builder /build/dist ./frontend/dist

# Runtime data directories (datasets mounted via volume; these hold session artifacts)
RUN mkdir -p backend/saved_data backend/session_memmaps

EXPOSE 8000

# Run from /app so relative paths (../frontend/dist, saved_data/) resolve correctly
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
