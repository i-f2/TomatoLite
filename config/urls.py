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
    path("healthz/", healthz),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    *[path(f"api/{n}/", include(f"apps.{n}.urls")) for n in API_APPS],
]
