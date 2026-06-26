from fastapi import FastAPI

app = FastAPI(title="HomeCloud", version="1.0")


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "version": "1.0"}
