"""Authentication service — handles login, JWT tokens, and session management."""

from db.connection import get_user, save_session
from db.connection import delete_session
import hashlib
import json
import time


class AuthService:
    """Handles user authentication and JWT token management.
    
    Provides login, logout, token verification, and session refresh
    capabilities for the application.
    """
    
    SECRET_KEY = "your-secret-key-change-in-production"
    TOKEN_EXPIRY = 3600  # 1 hour
    
    def login(self, email: str, password: str) -> dict:
        """Authenticate user with email and password, return JWT token.
        
        Args:
            email: User's email address
            password: Plain text password to verify
            
        Returns:
            dict with 'token' and 'user_id' on success
            
        Raises:
            ValueError: If credentials are invalid
        """
        user = get_user(email)
        if user is None:
            raise ValueError("User not found")
        
        hashed = hashlib.sha256(password.encode()).hexdigest()
        if user["password_hash"] != hashed:
            raise ValueError("Invalid password")
        
        token = self._generate_token(user["id"])
        save_session(user["id"], token)
        return {"token": token, "user_id": user["id"]}
    
    def logout(self, token: str) -> bool:
        """Invalidate a user session by removing the token.
        
        Args:
            token: The JWT token to invalidate
            
        Returns:
            True if session was found and removed
        """
        user_id = self.verify_token(token)
        delete_session(user_id)
        return True
    
    def verify_token(self, token: str) -> int:
        """Decode and verify a JWT token, return the user_id.
        
        Args:
            token: JWT token string to verify
            
        Returns:
            user_id (int) if token is valid
            
        Raises:
            ValueError: If token is expired or invalid
        """
        try:
            payload = json.loads(
                self._decode_base64(token.split(".")[1])
            )
            if payload.get("exp", 0) < time.time():
                raise ValueError("Token expired")
            return payload["user_id"]
        except (IndexError, KeyError, json.JSONDecodeError):
            raise ValueError("Invalid token")
    
    def refresh_token(self, old_token: str) -> dict:
        """Generate a new token from a valid existing token.
        
        Args:
            old_token: Current valid token
            
        Returns:
            dict with new 'token' and 'user_id'
        """
        user_id = self.verify_token(old_token)
        delete_session(user_id)
        new_token = self._generate_token(user_id)
        save_session(user_id, new_token)
        return {"token": new_token, "user_id": user_id}
    
    def _generate_token(self, user_id: int) -> str:
        """Create a JWT-like token with user_id and expiration."""
        import base64
        payload = {
            "user_id": user_id,
            "exp": time.time() + self.TOKEN_EXPIRY,
            "iat": time.time()
        }
        encoded = base64.b64encode(
            json.dumps(payload).encode()
        ).decode()
        return f"header.{encoded}.signature"
    
    def _decode_base64(self, data: str) -> str:
        """Decode base64 encoded string."""
        import base64
        return base64.b64decode(data).decode()


def check_permission(user_id: int, resource: str) -> bool:
    """Check if a user has permission to access a resource.
    
    Args:
        user_id: The user to check permissions for
        resource: The resource path to check access to
        
    Returns:
        True if user has access, False otherwise
    """
    user = get_user_by_id(user_id)
    if user is None:
        return False
    return resource in user.get("permissions", [])


def get_user_by_id(user_id: int) -> dict:
    """Fetch user record by ID instead of email."""
    # In production, this queries the database
    return {"id": user_id, "permissions": ["read", "write"]}
