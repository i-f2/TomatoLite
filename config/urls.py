from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import healthz

# S0-4：每 app 挂载在 /api/<name>/，占位 health 端点 → /api/<name>/health/
API_APPS = [
    "core",
    "cmdb",
    "tickets",
    "channels_a",
    "channels_b",
    "verification",
    "feedback",
    "metrics",
]

urlpatterns = [
    # M1-01：CMDB 的人工维护入口（DoD"Admin 可增删改查"）。
    # 静态样式说明：ASGI（daphne）下 admin 的 CSS/JS 不由 Django 自动服务，
    # 要看完整样式用 `manage.py runserver`（DEBUG=True 时自动服务 static）。
    path("admin/", admin.site.urls),
    path("healthz/", healthz),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    *[path(f"api/{n}/", include(f"apps.{n}.urls")) for n in API_APPS],
]
