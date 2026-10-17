FROM python:3.11-slim

WORKDIR /app

# System dependencies for ML libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Default: CPU torch (smaller). For GPU LSTM set build-arg:
#   --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX} torch==2.4.1 \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
