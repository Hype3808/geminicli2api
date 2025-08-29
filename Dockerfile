# Use official Python image as base
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (default 8888, can be overridden by env)
EXPOSE 8888

# Set environment variables (can be overridden by docker-compose)
ENV HOST=0.0.0.0 \
    PORT=8888

# Default command
CMD ["python", "run.py"]
