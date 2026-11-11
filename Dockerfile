# syntax=docker/dockerfile:1
#
# Image de production de Néo : serveur Rust + interface React.
# Ollama n'est PAS embarqué (voir docker-compose.yml) : le serveur s'y
# connecte via OLLAMA_URL.
#
#   docker build -t neo .
#   docker run --rm -p 8320:8320 -e OLLAMA_URL=http://host.docker.internal:11434 neo

# ---------------------------------------------------------------- frontend
FROM node:22-bookworm-slim AS frontend
WORKDIR /front
COPY code/frontend/package.json code/frontend/package-lock.json ./
RUN npm ci
COPY code/frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ rust
FROM rust:1.99-bookworm AS build
WORKDIR /src
COPY code/localai-rs/ ./
# Cache de dependances : couche separee du code applicatif.
RUN cargo build --release -p localai-server

# ------------------------------------------------------------------ image
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /src/target/release/localai-server /usr/local/bin/localai-server
COPY --from=frontend /front/dist /app/frontend/dist

ENV LOCALAI_ROOT=/app \
    LOCALAI_DATA=/app/data \
    LOCALAI_HOST=0.0.0.0 \
    LOCALAI_PORT=8320 \
    OLLAMA_URL=http://ollama:11434 \
    RUST_LOG=info

# Volumes attendus : /app/data (SQLite, documents) et /app/workspace.
RUN mkdir -p /app/data /app/workspace
VOLUME ["/app/data", "/app/workspace"]

EXPOSE 8320
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://127.0.0.1:8320/api/health || exit 1

CMD ["localai-server"]
