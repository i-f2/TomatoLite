"""S0-4 占位 health 接口（DoD：8×200）；业务端点自 M1 起进本包。"""

from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    return Response({"ok": True, "app": "core"})
