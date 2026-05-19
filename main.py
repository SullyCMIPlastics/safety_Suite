"""
CMI CMMS — backend API
Serves the static HTML app and provides a key-value store backed by SQLite.
Every browser STORE.set() fires a PUT here; on page load the browser fetches
all keys so any device sees the same data.
"""
import json, os, sqlite3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

DB_PATH  = os.environ.get("DB_PATH",  "/data/cmms.db")
APP_DIR  = os.environ.get("APP_DIR",  "/app/static")

app = FastAPI(docs_url=None, redoc_url=None)


# ── database helpers ──────────────────────────────────────────────────────────

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS store (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


# ── API routes (must be registered BEFORE the static-file catch-all) ─────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/store")
def get_all():
    """Return every key as a JSON object — used by the browser on page load."""
    conn = db()
    rows = conn.execute("SELECT key, value FROM store").fetchall()
    conn.close()
    return JSONResponse({row[0]: json.loads(row[1]) for row in rows})


@app.put("/api/store/{key}")
async def put_key(key: str, request: Request):
    """Accept raw JSON body and upsert into the store."""
    raw = await request.body()
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO store (key, value) VALUES (?, ?)",
        (key, raw.decode("utf-8")),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── static file serving (index.html + any future assets) ─────────────────────

os.makedirs(APP_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=APP_DIR, html=True), name="static")
