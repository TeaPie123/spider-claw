"""
作用：提供金额和订单接口的隔离测试夹具。
流程：校验商品输入 → 使用 Decimal 计算总价 → 返回报价；不参与任务服务运行。
"""

from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/orders")


class OrderItem(BaseModel):
    price: Decimal = Field(ge=0)
    quantity: int = Field(gt=0)


@router.post("/quote")
async def quote(items: list[OrderItem]) -> dict[str, Decimal]:
    return {"total": sum((item.price * item.quantity for item in items), Decimal("0"))}
