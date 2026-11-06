"""game-analyzer API — read-only visualization of the game generation pipeline."""

import logging
import sqlite3
from urllib.parse import unquote

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import config
import db
import graph_builder
import node_detail
import search as search_module
from image_utils import _FILENAME_RE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="game-analyzer")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/games")
def api_games():
    try:
        return {"games": graph_builder.list_games()}
    except sqlite3.OperationalError:
        logger.error("Failed to read game-server DB", exc_info=True)
        raise HTTPException(status_code=503, detail="game-server database unavailable")


@app.get("/api/games/{game_id}/graph")
def api_graph(game_id: str):
    graph = graph_builder.build_game_graph(game_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="game not found")
    return graph


@app.get("/api/games/{game_id}/nodes/{node_id}")
def api_node_detail(game_id: str, node_id: str):
    detail = node_detail.get_node_detail(game_id, node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="node not found")
    return detail


@app.get("/api/search")
def api_search(q: str = Query(min_length=2), game_id: str | None = None, limit: int = 30):
    try:
        return {"results": search_module.search_events(q, game_id, limit)}
    except sqlite3.OperationalError:
        logger.error("Search failed to read game-server DB", exc_info=True)
        raise HTTPException(status_code=503, detail="game-server database unavailable")


@app.get("/api/image")
def api_image(path: str = Query(...)):
    path = unquote(path)
    if not _FILENAME_RE.match(path):
        raise HTTPException(status_code=400, detail="invalid image path")
    file = Path(config.COMFYUI_FILES) / path
    if not file.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(file, media_type="image/png")


@app.post("/api/import-logs")
async def api_import_logs():
    """Trigger the one-shot legacy log import on game-server."""
    url = f"{config.GAME_SERVER_URL}/admin/import-log-events"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        logger.error("Legacy log import failed via %s", url, exc_info=True)
        raise HTTPException(status_code=502, detail="game-server import failed")


static_dir = Path(config.STATIC_DIR)
if static_dir.is_dir():
    # The SPA shell must always revalidate (a stale index.html keeps serving
    # a deleted hashed bundle after a redeploy); hashed assets never change.
    @app.middleware("http")
    async def spa_cache_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path == "/" or path == "/index.html":
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    logger.warning("Static frontend not found at %s (API-only mode)", static_dir)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
