FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn[standard]==0.34.0

COPY slack_mock_server.py .

EXPOSE 47892

CMD ["python", "slack_mock_server.py"]
