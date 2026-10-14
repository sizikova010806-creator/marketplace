"""
Order-сервис — CRUD для заказов.
In-memory хранилище. Минимальная версия для GraphQL (лаб. №3).
"""

import os
import itertools
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ============================================================
# 1. КОНФИГУРАЦИЯ
# ============================================================

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "order-1")

# ============================================================
# 2. IN-MEMORY ХРАНИЛИЩЕ
# ============================================================
# orders = { id: {"id": ..., "user_id": ..., "items": [...], "status": ...} }
orders_db: dict = {}
id_counter = itertools.count(1)

# ============================================================
# 3. МОДЕЛИ
# ============================================================

class OrderItem(BaseModel):
    product_id: int
    quantity: int
    price: float


class OrderCreate(BaseModel):
    user_id: str
    items: List[OrderItem]


class OrderUpdate(BaseModel):
    status: Optional[str] = None


# ============================================================
# 4. ПРИЛОЖЕНИЕ FASTAPI
# ============================================================

app = FastAPI(title="Order Service", version="1.0.0")


@app.get("/health")
def health():
    """Health check для Consul."""
    return {"status": "ok", "instance": INSTANCE_NAME}


@app.get("/orders")
def list_orders(user_id: Optional[str] = None):
    """Список заказов (можно фильтровать по user_id)."""
    orders = list(orders_db.values())
    if user_id:
        orders = [o for o in orders if o["user_id"] == user_id]
    return {
        "instance_id": INSTANCE_NAME,
        "count": len(orders),
        "orders": orders,
    }


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    """Получить заказ по ID."""
    order = orders_db.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"instance_id": INSTANCE_NAME, "order": order}


@app.post("/orders", status_code=201)
def create_order(req: OrderCreate):
    """Создать заказ."""
    if not req.items:
        raise HTTPException(status_code=400, detail="Order must have at least one item")

    order_id = next(id_counter)
    total = sum(item.price * item.quantity for item in req.items)
    order = {
        "id": order_id,
        "user_id": req.user_id,
        "items": [item.model_dump() for item in req.items],
        "status": "pending",
        "total": total,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    orders_db[order_id] = order
    return {"instance_id": INSTANCE_NAME, "order": order}


@app.put("/orders/{order_id}")
def update_order(order_id: int, req: OrderUpdate):
    """Обновить статус заказа."""
    order = orders_db.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if req.status:
        if req.status not in ["pending", "paid", "cancelled"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        order["status"] = req.status

    return {"instance_id": INSTANCE_NAME, "order": order}


@app.delete("/orders/{order_id}")
def delete_order(order_id: int):
    """Удалить заказ."""
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")
    deleted = orders_db.pop(order_id)
    return {"instance_id": INSTANCE_NAME, "deleted": deleted}


# ============================================================
# 5. РЕГИСТРАЦИЯ В CONSUL
# ============================================================

import httpx
import sys

CONSUL_HOST = os.getenv("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
SERVICE_PORT = int(os.getenv("PORT", "8000"))


def register_in_consul():
    """Регистрирует Order-сервис в Consul."""
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    registration = {
        "ID": service_id,
        "Name": "order",
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
    """Удаляет Order-сервис из Consul."""
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