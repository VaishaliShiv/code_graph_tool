"""Database connection and helper functions.

Provides low-level database operations for user management,
session storage, and order persistence.
"""

import sqlite3
from typing import Optional


DB_PATH = "app.db"


def get_connection():
    """Create and return a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_user(email: str) -> Optional[dict]:
    """Fetch a user record by email address.
    
    Args:
        email: The email to search for
        
    Returns:
        User dict if found, None otherwise
    """
    # Simulated for demo
    users = {
        "admin@example.com": {
            "id": 1, "email": "admin@example.com",
            "password_hash": "abc123", "role": "admin"
        },
        "user@example.com": {
            "id": 2, "email": "user@example.com", 
            "password_hash": "def456", "role": "user"
        }
    }
    return users.get(email)


def save_session(user_id: int, token: str) -> None:
    """Store an active session token for a user.
    
    Args:
        user_id: The user's ID
        token: The JWT token to store
    """
    # In production: INSERT INTO sessions (user_id, token, created_at)
    pass


def delete_session(user_id: int) -> None:
    """Remove all active sessions for a user.
    
    Args:
        user_id: The user whose sessions to delete
    """
    # In production: DELETE FROM sessions WHERE user_id = ?
    pass


def save_order(order: dict) -> int:
    """Persist a new order to the database.
    
    Args:
        order: Order dict with user_id, items, total, status
        
    Returns:
        The generated order_id
    """
    # In production: INSERT INTO orders ...
    return 12345


def get_order(order_id: int) -> Optional[dict]:
    """Fetch an order by its ID.
    
    Args:
        order_id: The order to retrieve
        
    Returns:
        Order dict if found, None otherwise
    """
    # Simulated
    return {
        "order_id": order_id, "user_id": 1,
        "items": [], "total": 79.98, "status": "pending"
    }


def run_migration():
    """Run database schema migrations.
    
    Creates tables if they don't exist:
    - users, sessions, orders, order_items
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT 'user'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            total REAL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
