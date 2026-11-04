"""uvicorn research_main:app --app-dir app --host 127.0.0.1 --port 8001"""
from research_backend.api import create_app

app = create_app()
