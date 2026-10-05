from __future__ import annotations

import logging
import sqlite3
import time

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="order-api")

DB_PATH = "orders.db"
INVENTORY_URL = "http://127.0.0.1:9002"
NOTIFY_URL = "http://127.0.0.1:9003"
NOTIFY_API_KEY = "demo-key"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("order-api")


def _init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "create table if not exists orders (id text primary key, sku text, qty integer, status text)"
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def startup() -> None:
    _init_db()


@app.post("/orders/{order_id}")
def create_order(order_id: str, sku: str = "BOOK-1", qty: int = 1) -> dict[str, str]:
    with httpx.Client(timeout=3.0) as client:
        stock = client.get(f"{INVENTORY_URL}/stock/{sku}")
        if stock.status_code != 200:
            raise HTTPException(status_code=502, detail="inventory unavailable")
        if stock.json()["qty"] < qty:
            raise HTTPException(status_code=409, detail="out of stock")

        reserve = client.post(f"{INVENTORY_URL}/reserve/{sku}", params={"amount": qty})
        if reserve.status_code != 200:
            raise HTTPException(status_code=409, detail="reserve failed")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "insert into orders(id, sku, qty, status) values (?, ?, ?, ?)",
            (order_id, sku, qty, "created"),
        )
        conn.commit()
    finally:
        conn.close()

    for attempt in range(3):
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.post(
                    f"{NOTIFY_URL}/notify",
                    params={"order_id": order_id},
                    headers={"x-api-key": NOTIFY_API_KEY},
                )
                response.raise_for_status()
            logger.info("notify_success order_id=%s attempt=%s", order_id, attempt + 1)
            return {"order_id": order_id, "status": "created"}
        except Exception as exc:
            logger.warning(
                "notify_retry order_id=%s attempt=%s error=%s",
                order_id,
                attempt + 1,
                str(exc),
            )
            time.sleep(0.3 * (attempt + 1))

    raise HTTPException(status_code=502, detail="notify failed after retries")
