FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir fastapi uvicorn redis httpx python-dotenv prometheus-fastapi-instrumentator
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]