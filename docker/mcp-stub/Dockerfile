# Installs fastmcp directly instead of from uv.lock, so it can drift from chat-service
FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi==0.141.1 fastmcp==3.4.7 uvicorn==0.52.0
WORKDIR /app
COPY server.py .

EXPOSE 8005
CMD ["python", "server.py"]
