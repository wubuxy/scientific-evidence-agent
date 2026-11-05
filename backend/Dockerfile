FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend/app
WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends ripgrep && rm -rf /var/lib/apt/lists/*
COPY requirements-research.txt constraints-ci.txt ./
RUN python -m pip install --no-cache-dir -r requirements-research.txt -c constraints-ci.txt
COPY app/ app/
COPY .env.research.example ./
COPY scripts/init_container.py scripts/init_container.py

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "research_main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8001"]
