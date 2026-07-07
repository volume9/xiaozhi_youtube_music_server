# Use full Python 3.10 image (not slim) for easier library installation
FROM python:3.10

# Update and install FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
# Upgrade pip first to avoid installation errors
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy all code
COPY . .

# Expose port 7071
EXPOSE 7071

# Run server with gevent worker for streaming (1 worker to share cache)
CMD ["gunicorn", "--bind", "0.0.0.0:7071", "--timeout", "300", "--workers", "1", "--worker-class", "gevent", "--worker-connections", "100", "app:app"]
