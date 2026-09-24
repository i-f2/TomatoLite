import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

from .routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    # RunLogConsumer 属 M1-16（§2.3）；骨架期 websocket 侧为空路由，通道可用即可
    "websocket": URLRouter(websocket_urlpatterns),
})
