"""Mock Jenkins（S0-6b）。规格=design-notes §3.8 端点表 + 故障模式清单；
回调签名 import 生产侧 `apps.core.hmac`（§6 第 3 条，单一实现）。"""
from .server import make_server  # noqa: F401
