FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init /app && git -C /app remote add origin https://github.com/mahmoud/boltons.git && git -C /app fetch --depth 1 origin 6505c6b5565275d484e0e3907a471944441f0c57 && git -C /app checkout --detach FETCH_HEAD && git -C /app remote remove origin
WORKDIR /app
