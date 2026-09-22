from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok', 'service': 'belgram-api', 'version': settings.app_version}


@app.get('/ready')
async def ready() -> dict[str, str]:
    return {'status': 'ready'}
