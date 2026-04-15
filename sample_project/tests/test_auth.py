"""Tests for the authentication service."""

from auth.service import AuthService, check_permission


class TestAuthService:
    """Test suite for AuthService login and token operations."""
    
    def test_login_success(self):
        """Test successful login returns a token."""
        svc = AuthService()
        result = svc.login("admin@example.com", "correct_password")
        assert "token" in result
        assert "user_id" in result
        assert result["user_id"] == 1
    
    def test_login_invalid_email(self):
        """Test login with non-existent email raises error."""
        svc = AuthService()
        try:
            svc.login("nobody@example.com", "password")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e).lower()
    
    def test_login_wrong_password(self):
        """Test login with wrong password raises error."""
        svc = AuthService()
        try:
            svc.login("admin@example.com", "wrong_password")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    
    def test_verify_token(self):
        """Test that a generated token can be verified."""
        svc = AuthService()
        result = svc.login("admin@example.com", "correct_password")
        user_id = svc.verify_token(result["token"])
        assert user_id == 1
    
    def test_verify_invalid_token(self):
        """Test that an invalid token raises error."""
        svc = AuthService()
        try:
            svc.verify_token("invalid.token.here")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    
    def test_refresh_token(self):
        """Test token refresh generates new token."""
        svc = AuthService()
        result = svc.login("admin@example.com", "correct_password")
        new_result = svc.refresh_token(result["token"])
        assert new_result["token"] != result["token"]
        assert new_result["user_id"] == result["user_id"]


class TestPermissions:
    """Test suite for permission checking."""
    
    def test_check_permission_allowed(self):
        """Test that users with correct permissions pass."""
        assert check_permission(1, "read") is True
    
    def test_check_permission_denied(self):
        """Test that users without permission are denied."""
        assert check_permission(1, "admin_panel") is False
