from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from .routers import tournament, case, users

app = FastAPI(
    title="Tournament Admin API",
    description="API for managing debate tournaments",
    version="0.1.0"
)

# Настройка CORS для работы с фронтендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Обслуживание статических файлов (фронтенд)
app.mount("/static", StaticFiles(directory="admin/static"), name="static")

# Маршрут для главной страницы
@app.get("/")
def serve_frontend():
    from fastapi.responses import FileResponse
    return FileResponse("admin/static/index.html")

# API роутеры
app.include_router(tournament.router, prefix="/api/tournament", tags=["tournament"])
app.include_router(case.router, prefix="/api/cases", tags=["cases"]) 
app.include_router(users.router, prefix="/api", tags=["users"]) 

if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        reload=True
    )
