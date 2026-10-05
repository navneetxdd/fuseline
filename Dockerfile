# syntax=docker/dockerfile:1

# ---- Stage 1: build the React UI ----
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: API + built UI in one small image ----
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FUSELINE_DATA_DIR=/data
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY --from=ui /ui/dist ./frontend/dist

# Case databases and evidence copies live on a volume, owned by an unprivileged user.
RUN useradd --create-home --uid 10001 fuseline \
    && mkdir -p /data \
    && chown fuseline:fuseline /data
USER fuseline
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

# Publish the port on loopback only (see docker-compose.yml): Fuseline has no login.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
