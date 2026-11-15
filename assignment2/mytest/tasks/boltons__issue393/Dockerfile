FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init /app && git -C /app remote add origin https://github.com/mahmoud/boltons.git && git -C /app fetch --depth 1 origin ce7c7d2b15ea189c1284a48d742a7862bfae8606 && git -C /app checkout --detach FETCH_HEAD && git -C /app remote remove origin
WORKDIR /app
