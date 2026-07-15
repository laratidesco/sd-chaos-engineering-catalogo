import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-gateway-naive")

PRODUCT_SERVICE_URL = "http://product-service-naive:8001"

http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=None)
    yield
    await http_client.aclose()


app = FastAPI(title="API Gateway (naive, no fault tolerance)", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


class ProductIn(BaseModel):
    name: str
    price: float
    stock: int


async def call_product_service(method: str, path: str, **kwargs) -> httpx.Response:
    response = await http_client.request(method, f"{PRODUCT_SERVICE_URL}{path}", **kwargs)
    response.raise_for_status()
    return response


def _raise_for_downstream_error(exc: Exception):
    if isinstance(exc, httpx.HTTPStatusError):
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    raise HTTPException(status_code=502, detail="product service error") from exc


@app.get("/products")
async def list_products():
    try:
        response = await call_product_service("GET", "/products")
    except Exception as exc:
        _raise_for_downstream_error(exc)
    return response.json()


@app.post("/products", status_code=201)
async def create_product(product: ProductIn):
    try:
        response = await call_product_service("POST", "/products", json=product.model_dump())
    except Exception as exc:
        _raise_for_downstream_error(exc)
    return response.json()


@app.get("/products/{product_id}")
async def get_product(product_id: int):
    try:
        response = await call_product_service("GET", f"/products/{product_id}")
    except Exception as exc:
        _raise_for_downstream_error(exc)
    return response.json()


@app.delete("/products/{product_id}", status_code=204)
async def delete_product(product_id: int):
    try:
        await call_product_service("DELETE", f"/products/{product_id}")
    except Exception as exc:
        _raise_for_downstream_error(exc)
    return Response(status_code=204)


@app.get("/health")
async def health():
    return {"status": "ok"}
