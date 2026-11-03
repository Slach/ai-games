FROM node:26-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM astral/uv:python3.14-bookworm-slim

RUN apt update && apt install -y curl
WORKDIR /app/backend

# Install dependencies
COPY backend/requirements.txt .
RUN uv pip install --no-cache --system --break-system-packages -r requirements.txt

# Backend application (compose bind-mounts ./game-analyzer/ over /app, which
# also contains backend/ — same layout; the built frontend lives in /srv/static
# so the mount cannot shadow it)
COPY backend/ .
COPY --from=frontend /src/dist /srv/static

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
