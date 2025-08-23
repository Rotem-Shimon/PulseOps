# ==============================
# Collector Dockerfile (simple)
# ==============================
FROM python:3.10-slim

# Workdir
WORKDIR /app

# Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code
COPY . .

# Run as non-root (best practice)
RUN useradd -m appuser
USER appuser

# Stream logs immediately (good for Docker logs)
ENV PYTHONUNBUFFERED=1

# Run the collector
CMD ["python", "collector.py"]
