"""
作用：验证迁入测试目录的订单夹具与金额边界。
流程：挂载夹具路由 → 提交正常、空列表和无效商品 → 核对 Decimal 报价和校验响应。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.order_service import router


@pytest.fixture
def client():
    application = FastAPI()
    application.include_router(router)
    with TestClient(application) as value:
        yield value


def test_quote_multiple_items(client):
    response = client.post("/orders/quote", json=[{"price": "10.50", "quantity": 2}, {"price": "5.25", "quantity": 3}])
    assert response.status_code == 200
    assert response.json()["total"] == "36.75"


def test_quote_empty_items(client):
    assert client.post("/orders/quote", json=[]).json()["total"] == "0"


def test_quote_rejects_negative_price(client):
    assert client.post("/orders/quote", json=[{"price": "-1", "quantity": 1}]).status_code == 422
