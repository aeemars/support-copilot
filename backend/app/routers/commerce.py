from fastapi import APIRouter, HTTPException

from app.models import CartItemRequest, CheckoutRequest, ReturnRequest
from app.services.commerce_service import (
    add_to_cart, checkout, get_cart, list_products, order_detail, product_detail, remove_from_cart, request_return, user_orders,
    refund_detail, return_detail, user_refunds, user_returns,
)

router = APIRouter(prefix="/api", tags=["commerce"])


@router.get("/products")
async def products():
    return await list_products()


@router.get("/products/{product_id}")
async def product(product_id: str):
    result = await product_detail(product_id)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found.")
    return result


@router.get("/cart/{user_id}")
async def cart(user_id: str):
    return await get_cart(user_id)


@router.post("/cart/{user_id}/items")
async def cart_add(user_id: str, item: CartItemRequest):
    try:
        return await add_to_cart(user_id, item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/cart/{user_id}/items/{product_id}")
async def cart_remove(user_id: str, product_id: str):
    return await remove_from_cart(user_id, product_id)


@router.post("/checkout")
async def checkout_order(request: CheckoutRequest):
    try:
        return await checkout(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/orders/{user_id}")
async def orders(user_id: str):
    return await user_orders(user_id)


@router.get("/orders/{user_id}/{order_id}")
async def order(user_id: str, order_id: str):
    result = await order_detail(user_id, order_id)
    if not result:
        raise HTTPException(status_code=404, detail="Order not found.")
    return result


@router.post("/orders/{user_id}/{order_id}/returns")
async def create_return(user_id: str, order_id: str, request: ReturnRequest):
    if request.user_id != user_id:
        raise HTTPException(status_code=403, detail="User mismatch.")
    try:
        return await request_return(order_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/returns/{user_id}")
async def returns(user_id: str):
    return await user_returns(user_id)


@router.get("/returns/{user_id}/{return_id}")
async def return_status(user_id: str, return_id: str):
    result = await return_detail(user_id, return_id)
    if not result:
        raise HTTPException(status_code=404, detail="Return not found.")
    return result


@router.get("/refunds/{user_id}")
async def refunds(user_id: str):
    return await user_refunds(user_id)


@router.get("/refunds/{user_id}/{refund_id}")
async def refund_status(user_id: str, refund_id: str):
    result = await refund_detail(user_id, refund_id)
    if not result:
        raise HTTPException(status_code=404, detail="Refund not found.")
    return result
