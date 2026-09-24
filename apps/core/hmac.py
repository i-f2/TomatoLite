"""HMAC 签名/验签的**单一实现**（§6 第 3 条：假服务 import 生产代码，不得各自再写一遍）。

签名口径逐字 §3.3：`compare_digest(sha256(secret, f"{ts}.{raw}"), header)`——
string-to-sign = `f"{ts}.{raw}"`（`raw` 为**原始字节**，禁止 json 重序列化，§3.3 首坑）。
头名钉定（设计文档未钉，S0-6b Spec 拍板）：`X-TL-Signature: sha256=<hex>` +
`X-TL-Timestamp: <epoch 秒>`。时间窗防重放 `|now-ts| <= window_s`（默认 300s）。
per-job secret 由调用方传入（配置解析归 M2-05），本模块零 Django、零 settings——
mock 进程无 Django 环境直接 import（有双重断言锁死）。
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIG_HEADER = "X-TL-Signature"
TS_HEADER = "X-TL-Timestamp"
PREFIX = "sha256="
DEFAULT_WINDOW_S = 300


def sign(secret: str, ts: int, raw: bytes) -> str:
    """对 (ts, 原始字节) 出签。raw 必须是 bytes——str 一律拒绝，
    把"先 loads 再 dumps 重序列化"这类错误摁在类型层。"""
    if not isinstance(raw, bytes):
        raise TypeError("sign() 必须对原始字节计算，收到 %s——禁止重序列化" % type(raw).__name__)
    sts = f"{ts}.".encode() + raw
    return PREFIX + hmac.new(secret.encode(), sts, hashlib.sha256).hexdigest()


def verify(
    secret: str,
    raw: bytes,
    header: str | None,
    ts,
    *,
    window_s: int = DEFAULT_WINDOW_S,
    now: int | None = None,
) -> bool:
    """常量时间比对 + 时间窗。任何缺项/越窗/格式错都拒（返回 False，不抛）。"""
    if header is None or not isinstance(raw, bytes):
        return False
    if not header.startswith(PREFIX):
        return False
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False
    now_int = int(now if now is not None else time.time())
    if abs(now_int - ts_int) > window_s:  # 防重放时间窗（§3.3）
        return False
    return hmac.compare_digest(sign(secret, ts_int, raw), header)  # 常量时间（§1.6 规则 2）
