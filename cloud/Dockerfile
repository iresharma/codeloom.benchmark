FROM python:3.12-slim
WORKDIR /app
COPY monitor.py dashboard.html ./
ENV PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
CMD ["python", "monitor.py"]
