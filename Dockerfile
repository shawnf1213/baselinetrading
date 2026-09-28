# One image: builds the React UI, then serves it and the API from FastAPI.
# Secrets are passed at runtime (docker run -e ...), never baked into the image.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .
COPY config/ config/
COPY --from=ui /ui/dist frontend/dist
# The package resolves config/, logs/, state/ and frontend/dist relative to the source tree.
ENV PYTHONPATH=/app/src
EXPOSE 8000
# logs/ (the journal) and state/ (the kill switch) should be mounted volumes so they survive restarts.
CMD ["uvicorn", "baselinetrading.server:app", "--host", "0.0.0.0", "--port", "8000"]
