"""
Auth-сервис — регистрация, вход, выдача JWT.
In-memory хранилище пользователей (словарь Python).
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from pydantic import BaseModel

# ============================================================
# 1. КОНФИГУРАЦИЯ
# ============================================================

SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "auth-1")

# ============================================================
# 2. IN-MEMORY ХРАНИЛИЩЕ
# ============================================================
# users = { "username": {"username": "...", "password_hash": "..."} }
users_db: dict = {}

# ============================================================
# 3. УТИЛИТЫ
# ============================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def hash_password(password: str) -> str:
    """Хеширует пароль через bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверяет, совпадает ли пароль с хешем."""
    return pwd_context.verify(plain, hashed)


def create_access_token(username: str) -> str:
    """Создаёт JWT-токен."""
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Проверяет и декодирует JWT."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Dependency: извлекает username из токена."""
    payload = decode_token(credentials.credentials)
    username = payload.get("sub")
    if username is None or username not in users_db:
        raise HTTPException(status_code=401, detail="User not found")
    return username


# ============================================================
# 4. МОДЕЛИ ЗАПРОСОВ
# ============================================================

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ============================================================
# 5. ПРИЛОЖЕНИЕ FASTAPI
# ============================================================

app = FastAPI(title="Auth Service", version="1.0.0")


@app.get("/health")
def health():
    """Health check для Consul."""
    return {"status": "ok", "instance": INSTANCE_NAME}


@app.post("/register", status_code=201)
def register(req: RegisterRequest):
    """Регистрация нового пользователя."""
    if req.username in users_db:
        raise HTTPException(status_code=400, detail="User already exists")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Password too short")

    users_db[req.username] = {
        "username": req.username,
        "password_hash": hash_password(req.password),
    }
    return {"message": "User registered", "username": req.username}


@app.post("/login")
def login(req: LoginRequest):
    """Вход — возвращает JWT-токен."""
    user = users_db.get(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(req.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@app.get("/me")
def me(username: str = Depends(get_current_user)):
    """Защищённый эндпоинт — возвращает данные пользователя."""
    return {
        "username": username,
        "instance": INSTANCE_NAME,
        "authenticated": True,
    }


# ============================================================
# 6. РЕГИСТРАЦИЯ В CONSUL
# ============================================================

import httpx
import sys

CONSUL_HOST = os.getenv("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
SERVICE_PORT = int(os.getenv("PORT", "8000"))


def register_in_consul():
    """Регистрирует Auth-сервис в Consul."""
    service_id = f"{INSTANCE_NAME}-{SERVICE_PORT}"
    registration = {
        "ID": service_id,
        "Name": "auth",
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
    """Удаляет Auth-сервис из Consul."""
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
# 7. ЗАПУСК
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)