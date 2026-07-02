import logging
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-gateway")

PRODUCT_SERVICE_URL = "http://product-service:8001"
REQUEST_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)
RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """Trips after consecutive downstream failures; a single trial request
    after recovery_timeout decides whether to close again or re-open."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 15.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.opened_at = 0.0

    def before_call(self):
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.warning("circuit breaker half-open: allowing a trial request")
            else:
                raise CircuitOpenError()

    def on_success(self):
        if self.state != CircuitState.CLOSED:
            logger.info("circuit breaker closed after successful trial request")
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def on_failure(self):
        self.failure_count += 1
        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
            logger.error("circuit breaker opened after %d failure(s)", self.failure_count)


circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=15.0)
http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    yield
    await http_client.aclose()


app = FastAPI(title="API Gateway", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


class ProductIn(BaseModel):
    name: str
    price: float
    stock: int


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    reraise=True,
)
async def _send_request(method: str, path: str, **kwargs) -> httpx.Response:
    response = await http_client.request(method, f"{PRODUCT_SERVICE_URL}{path}", **kwargs)
    response.raise_for_status()
    return response


async def call_product_service(method: str, path: str, **kwargs) -> httpx.Response:
    circuit_breaker.before_call()
    try:
        response = await _send_request(method, path, **kwargs)
    except CircuitOpenError:
        raise
    except Exception:
        circuit_breaker.on_failure()
        raise
    circuit_breaker.on_success()
    return response


def _raise_for_downstream_error(exc: Exception):
    if isinstance(exc, CircuitOpenError):
        raise HTTPException(status_code=503, detail="product service unavailable (circuit open)") from exc
    if isinstance(exc, httpx.HTTPStatusError):
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        raise HTTPException(status_code=504, detail="product service timeout") from exc
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
    return {"status": "ok", "circuit_state": circuit_breaker.state}
