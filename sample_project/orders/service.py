"""Order processing service — handles order creation, validation, and fulfillment."""

from auth.service import AuthService
from db.connection import get_user, save_order, get_order


auth = AuthService()


class OrderService:
    """Manages the complete order lifecycle.
    
    Handles order creation, validation, status updates,
    and order history retrieval.
    """
    
    def place_order(self, token: str, items: list) -> dict:
        """Create a new order after verifying user authentication.
        
        Args:
            token: JWT auth token
            items: List of item dicts with 'product_id' and 'quantity'
            
        Returns:
            dict with 'order_id', 'user_id', 'total', 'status'
        """
        user_id = auth.verify_token(token)
        
        total = self._calculate_total(items)
        if total <= 0:
            raise ValueError("Order total must be positive")
        
        order = {
            "user_id": user_id,
            "items": items,
            "total": total,
            "status": "pending"
        }
        order_id = save_order(order)
        return {"order_id": order_id, "user_id": user_id, 
                "total": total, "status": "pending"}
    
    def get_order_status(self, token: str, order_id: int) -> dict:
        """Retrieve the current status of an order.
        
        Args:
            token: JWT auth token  
            order_id: The order to look up
            
        Returns:
            Order dict with current status
        """
        user_id = auth.verify_token(token)
        order = get_order(order_id)
        if order is None:
            raise ValueError("Order not found")
        if order["user_id"] != user_id:
            raise PermissionError("Not your order")
        return order
    
    def cancel_order(self, token: str, order_id: int) -> bool:
        """Cancel a pending order.
        
        Args:
            token: JWT auth token
            order_id: The order to cancel
            
        Returns:
            True if successfully cancelled
        """
        order = self.get_order_status(token, order_id)
        if order["status"] != "pending":
            raise ValueError("Can only cancel pending orders")
        # Update order status...
        return True
    
    def _calculate_total(self, items: list) -> float:
        """Calculate the total price for a list of items."""
        total = 0.0
        for item in items:
            price = self._get_item_price(item["product_id"])
            total += price * item.get("quantity", 1)
        return round(total, 2)
    
    def _get_item_price(self, product_id: int) -> float:
        """Look up the price of a product."""
        # In production, this queries a product catalog
        prices = {1: 29.99, 2: 49.99, 3: 9.99}
        return prices.get(product_id, 0.0)


def get_user_orders(token: str) -> list:
    """Get all orders for the authenticated user."""
    user_id = auth.verify_token(token)
    # In production, query orders by user_id
    return []
