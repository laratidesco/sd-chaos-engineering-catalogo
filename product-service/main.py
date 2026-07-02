import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import Column, Float, Integer, String, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("product-service")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/catalog"
)

engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=10)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    stock = Column(Integer, nullable=False, default=0)


class ProductIn(BaseModel):
    name: str
    price: float
    stock: int


class ProductOut(ProductIn):
    id: int

    model_config = ConfigDict(from_attributes=True)


async def _wait_for_database(max_attempts: int = 10, initial_delay: float = 1.0):
    """Postgres and this service start at roughly the same time in Kubernetes,
    so the first connection attempts may hit it before it accepts connections."""
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except (OperationalError, OSError):
            if attempt == max_attempts:
                raise
            logger.warning(
                "database not ready yet (attempt %d/%d), retrying in %.1fs",
                attempt, max_attempts, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _wait_for_database()
    yield
    await engine.dispose()


app = FastAPI(title="Product Service", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


@app.get("/products", response_model=list[ProductOut])
async def list_products():
    async with SessionLocal() as session:
        result = await session.execute(select(Product))
        return result.scalars().all()


@app.post("/products", response_model=ProductOut, status_code=201)
async def create_product(product: ProductIn):
    async with SessionLocal() as session:
        db_product = Product(**product.model_dump())
        session.add(db_product)
        await session.commit()
        await session.refresh(db_product)
        return db_product


@app.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: int):
    async with SessionLocal() as session:
        db_product = await session.get(Product, product_id)
        if db_product is None:
            raise HTTPException(status_code=404, detail="product not found")
        return db_product


@app.delete("/products/{product_id}", status_code=204)
async def delete_product(product_id: int):
    async with SessionLocal() as session:
        db_product = await session.get(Product, product_id)
        if db_product is None:
            raise HTTPException(status_code=404, detail="product not found")
        await session.delete(db_product)
        await session.commit()


@app.get("/health")
async def health():
    async with SessionLocal() as session:
        await session.execute(select(1))
    return {"status": "ok"}
