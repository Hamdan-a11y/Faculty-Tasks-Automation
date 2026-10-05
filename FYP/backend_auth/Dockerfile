# Use Python
FROM python:3.11-slim

# 1. Install system utilities and Chrome/Chromedriver dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    containerd \
    chromium \
    chromium-driver \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for Chrome
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 2. Set up the app
WORKDIR /app
COPY requirements.txt /app/requirements.txt

# 3. Install Python libraries
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the project code
COPY . /app

# 5. Command to run the server
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"]