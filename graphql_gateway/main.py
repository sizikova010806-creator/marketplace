"""
GraphQL Gateway — объединяет данные из Catalog и Order.
Клиент делает один GraphQL-запрос — Gateway идёт в сервисы по HTTP.
"""

import os
from typing import List, Optional

import httpx
import strawberry
from strawberry.fastapi import GraphQLRouter
from fastapi import FastAPI

# ============================================================
# 1. КОНФИГУРАЦИЯ
# ============================================================

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "graphql-gateway-1")
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog-1:8000")
ORDER_URL = os.getenv("ORDER_URL", "http://order-1:8000")

# ============================================================
# 2. GRAPHQL-ТИПЫ (схема)
# ============================================================

@strawberry.type(description="Товар из каталога")
class Product:
    id: int = strawberry.field(description="Уникальный ID товара")
    name: str = strawberry.field(description="Название товара")
    price: float = strawberry.field(description="Цена в рублях")
    category: str = strawberry.field(description="Категория товара")


@strawberry.type(description="Позиция в заказе")
class OrderItem:
    product_id: int = strawberry.field(description="ID товара из Catalog")
    quantity: int = strawberry.field(description="Количество штук")
    price: float = strawberry.field(description="Цена на момент заказа")


@strawberry.type(description="Заказ пользователя")
class Order:
    id: int = strawberry.field(description="Уникальный ID заказа")
    user_id: str = strawberry.field(description="ID пользователя")
    status: str = strawberry.field(description="Статус: pending, paid, cancelled")
    total: float = strawberry.field(description="Итоговая сумма заказа")
    items: List[OrderItem] = strawberry.field(description="Позиции заказа")


@strawberry.type(description="Заказ с информацией о товарах из Catalog")
class OrderWithProducts:
    order: Order = strawberry.field(description="Данные заказа")
    products: List[Product] = strawberry.field(description="Товары, входящие в заказ")


# ============================================================
# 3. РЕЗОЛВЕРЫ (логика запросов)
# ============================================================

async def fetch_products() -> List[dict]:
    """Получить все товары из Catalog."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{CATALOG_URL}/products", timeout=5.0)
        response.raise_for_status()
        return response.json().get("products", [])

# ============================================================
# КЭШ ТОВАРОВ (решение проблемы N+1)
# ============================================================

_products_cache: dict = {}
_cache_timestamp: float = 0
CACHE_TTL = 30  # секунд


async def fetch_products_cached() -> dict:
    """
    Получить товары с кэшированием.
    Возвращает словарь {product_id: product}.
    Кэш живёт CACHE_TTL секунд.
    """
    global _products_cache, _cache_timestamp
    import time

    now = time.time()
    if now - _cache_timestamp > CACHE_TTL or not _products_cache:
        data = await fetch_products()
        _products_cache = {p["id"]: p for p in data}
        _cache_timestamp = now
        print(f"[CACHE] Products cached: {len(_products_cache)} items")

    return _products_cache


async def fetch_orders(user_id: Optional[str] = None) -> List[dict]:
    """Получить заказы из Order."""
    url = f"{ORDER_URL}/orders"
    if user_id:
        url += f"?user_id={user_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=5.0)
        response.raise_for_status()
        return response.json().get("orders", [])


@strawberry.type
class Query:

    @strawberry.field
    async def products(self) -> List[Product]:
        """Все товары из Catalog."""
        data = await fetch_products()
        return [Product(**p) for p in data]

    @strawberry.field
    async def orders(self, user_id: Optional[str] = None) -> List[Order]:
        """Заказы (можно фильтровать по user_id)."""
        data = await fetch_orders(user_id)
        result = []
        for o in data:
            items = [OrderItem(**item) for item in o["items"]]
            result.append(Order(
                id=o["id"],
                user_id=o["user_id"],
                status=o["status"],
                total=o["total"],
                items=items,
            ))
        return result

    @strawberry.field
    async def orders_with_products(self, user_id: str) -> List[OrderWithProducts]:
        """
        Заказы пользователя + товары из Catalog.
        Это ключевой запрос: объединяет данные из двух сервисов.
        
        Использует кэш товаров — решает проблему N+1.
        """
        orders_data = await fetch_orders(user_id)
        products_by_id_raw = await fetch_products_cached()

        result = []
        for o in orders_data:
            items = [OrderItem(**item) for item in o["items"]]
            order = Order(
                id=o["id"],
                user_id=o["user_id"],
                status=o["status"],
                total=o["total"],
                items=items,
            )
            products_in_order = [
                Product(**products_by_id_raw[item["product_id"]])
                for item in o["items"]
                if item["product_id"] in products_by_id_raw
            ]
            result.append(OrderWithProducts(order=order, products=products_in_order))
        return result


# ============================================================
# 4. ПРИЛОЖЕНИЕ
# ============================================================

schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

app = FastAPI(title="GraphQL Gateway", version="1.0.0")
app.include_router(graphql_app, prefix="/graphql")


@app.get("/health")
def health():
    """Health check для Consul."""
    return {"status": "ok", "instance": INSTANCE_NAME}


# ============================================================
# 5. РЕГИСТРАЦИЯ В CONSUL
# ============================================================

import sys

CONSUL_HOST = os.getenv("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
SERVICE_PORT = int(os.getenv("PORT", "8000"))


def register_in_consul():
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    registration = {
        "ID": service_id,
        "Name": "graphql-gateway",
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
        sys.stdout.write(f"\n[CONSUL] Registered {service_id}: {response.status_code}\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"\n[CONSUL] Registration failed: {e}\n")
        sys.stdout.flush()


def deregister_from_consul():
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    url = f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/deregister/{service_id}"
    try:
        httpx.put(url, timeout=5.0)
        sys.stdout.write(f"\n[CONSUL] Deregistered {service_id}\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"\n[CONSUL] Deregistration failed: {e}\n")
        sys.stdout.flush()


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