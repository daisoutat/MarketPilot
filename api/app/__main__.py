# Local dev: export MP_AUTH_MODE=dev and MP_DATABASE_URL=postgresql+asyncpg://...
# (docker-compose at repo root provides a disposable Postgres 16.)
from app.main import app

# Convenience: `python -m app` runs the dev server on :8000.
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)