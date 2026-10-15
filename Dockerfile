# One container for Cloud Run: Angular build (served as static files) + FastAPI API under /api.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npx ng build --configuration production

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
COPY backend/data data
COPY backend/.cache .cache
COPY --from=ui /ui/dist/frontend/browser static
# `root` mounts the API at /api and the UI at /
CMD exec uvicorn app.main:root --host 0.0.0.0 --port ${PORT}
