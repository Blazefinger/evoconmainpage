from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os

app = FastAPI()

def load_html():
    path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

HTML = load_html()

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML

@app.get("/health")
def health():
    return {"ok": True}
