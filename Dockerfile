FROM python:3.9-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for building some python packages)
# RUN apt-get update && apt-get install -y gcc

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose the configured port
EXPOSE 5300

# Run with Gunicorn (1 worker to maintain single scheduler instance, threaded for concurrency)
CMD ["gunicorn", "--workers=1", "--threads=4", "--bind=0.0.0.0:5300", "app:app"]
