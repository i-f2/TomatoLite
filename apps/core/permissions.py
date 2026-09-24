"""S0-5（拍板④）：RBAC 权限类。默认权限保持开放（health 匿名 200 是回归红线），
受控端点在**视图层显式声明** `required_roles`——缺声明按配置错误炸，
不许静默放行也不许静默 403（两种静默都会把越权/误锁写进"全绿"里）。
"""
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission

from .exceptions import AppError


class HasRole(BasePermission):
    """视图须声明 `required_roles: tuple[Role, ...]`。

    - 匿名 → 401（AppError，经全局 handler 落 envelope）
    - 已认证但角色不在集合 → 403
    - system **不天然在任何集合里**——需放行的位点显式列出（拍板②③）
    """

    def has_permission(self, request, view):
        required = getattr(view, "required_roles", None)
        if required is None:
            raise ImproperlyConfigured(
                f"{type(view).__name__} 使用 HasRole 但未声明 required_roles")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise AppError("未认证", code="NOT_AUTHENTICATED", status_code=401)
        if getattr(user, "role", None) not in required:
            raise AppError("角色无权", code="PERMISSION_DENIED", status_code=403)
        return True
