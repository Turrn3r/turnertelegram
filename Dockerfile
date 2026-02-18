FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app ./app
COPY static ./static

EXPOSE 8080

# Use uvicorn with proper host binding for Fly.io
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
