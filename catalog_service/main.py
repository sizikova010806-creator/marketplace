"""
Catalog-сервис — CRUD для товаров.
In-memory хранилище. Возвращает instance_id для доказательства балансировки.
"""

import os
import itertools
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ============================================================
# 1. КОНФИГУРАЦИЯ
# ============================================================

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "catalog-1")

# ============================================================
# 2. IN-MEMORY ХРАНИЛИЩЕ
# ============================================================
products_db: dict = {}
id_counter = itertools.count(1)

# ============================================================
# 3. МОДЕЛИ
# ============================================================

class ProductCreate(BaseModel):
    name: str
    price: float
    category: str = "general"


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None


# ============================================================
# 4. ПРИЛОЖЕНИЕ FASTAPI
# ============================================================

from fastapi.openapi.docs import get_swagger_ui_html

app = FastAPI(
    title="Catalog Service",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Swagger UI, который ищет openapi.json по правильному пути."""
    return get_swagger_ui_html(
        openapi_url="/catalog/openapi.json",
        title="Catalog Service — Swagger UI",
    )

@app.get("/health")
def health():
    """Health check для Consul."""
    return {"status": "ok", "instance": INSTANCE_NAME}


@app.get("/products")
def list_products(page: int = 1, size: int = 20):
    """Список товаров с пагинацией."""
    all_products = list(products_db.values())
    total = len(all_products)
    start = (page - 1) * size
    end = start + size
    paginated = all_products[start:end]
    return {
        "instance_id": INSTANCE_NAME,
        "page": page,
        "size": size,
        "total": total,
        "products": paginated,
    }


@app.get("/products/{product_id}")
def get_product(product_id: int):
    """Получить товар по ID."""
    product = products_db.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"instance_id": INSTANCE_NAME, "product": product}


@app.post("/products", status_code=201)
def create_product(req: ProductCreate):
    """Создать товар."""
    if req.price < 0:
        raise HTTPException(status_code=400, detail="Price cannot be negative")

    product_id = next(id_counter)
    product = {
        "id": product_id,
        "name": req.name,
        "price": req.price,
        "category": req.category,
    }
    products_db[product_id] = product
    return {"instance_id": INSTANCE_NAME, "product": product}


@app.put("/products/{product_id}")
def update_product(product_id: int, req: ProductUpdate):
    """Обновить товар."""
    product = products_db.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if req.name is not None:
        product["name"] = req.name
    if req.price is not None:
        if req.price < 0:
            raise HTTPException(status_code=400, detail="Price cannot be negative")
        product["price"] = req.price
    if req.category is not None:
        product["category"] = req.category

    return {"instance_id": INSTANCE_NAME, "product": product}


@app.delete("/products/{product_id}")
def delete_product(product_id: int):
    """Удалить товар."""
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    deleted = products_db.pop(product_id)
    return {"instance_id": INSTANCE_NAME, "deleted": deleted}


# ============================================================
# 5. РЕГИСТРАЦИЯ В CONSUL
# ============================================================

import httpx
import socket
import atexit

CONSUL_HOST = os.getenv("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
SERVICE_PORT = int(os.getenv("PORT", "8000"))


def register_in_consul():
    """Регистрирует сервис в Consul при старте."""
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    registration = {
        "ID": service_id,
        "Name": "catalog",
        "Address": INSTANCE_NAME,
        "Port": SERVICE_PORT,
        "Check": {
            "HTTP": f"http://{INSTANCE_NAME}:{SERVICE_PORT}/health",
            "Interval": "10s",
            "Timeout": "3s",
        },
    }
    url = f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/register"
    try:
        response = httpx.put(url, json=registration, timeout=5.0)
        print(f"[CONSUL] Registered {service_id}: {response.status_code}")
    except Exception as e:
        print(f"[CONSUL] Registration failed: {e}")


def deregister_from_consul():
    """Удаляет сервис из Consul при остановке."""
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    url = f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/deregister/{service_id}"
    try:
        httpx.put(url, timeout=5.0)
        print(f"[CONSUL] Deregistered {service_id}")
    except Exception as e:
        print(f"[CONSUL] Deregistration failed: {e}")


@app.on_event("startup")
def on_startup():
    register_in_consul()


@app.on_event("shutdown")
def on_shutdown():
    deregister_from_consul()


# ============================================================
# 6. ЗАПУСК
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)