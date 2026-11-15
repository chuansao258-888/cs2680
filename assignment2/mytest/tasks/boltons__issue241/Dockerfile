FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init /app && git -C /app remote add origin https://github.com/mahmoud/boltons.git && git -C /app fetch --depth 1 origin 9c99fd6e4a345803a43003284cd2aed8a772eb01 && git -C /app checkout --detach FETCH_HEAD && git -C /app remote remove origin
WORKDIR /app
