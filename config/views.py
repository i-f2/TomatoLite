"""S0-3 骨架端点。业务 API 一律从 S0-4 起（§0.1 app 划分之后）。"""

from django.http import JsonResponse


def healthz(request):
    """compose healthcheck 用；刻意不查 DB——骨架期 PG 不在 compose 内（§0.6a）。"""
    return JsonResponse({"ok": True, "scaffold": "S0-3"})
