from fastapi import FastAPI

from app.api.v1.router import api_router


app = FastAPI(title='Lusi v1.0 API', version='1.0.0')
app.include_router(api_router, prefix='/api/v1')


@app.get('/health')
def health_check() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/')
def root() -> dict[str, str]:
    return {'message': 'Lusi v1.0 backend is running'}
