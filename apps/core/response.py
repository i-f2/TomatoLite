"""统一响应包装（S0-4 拍板默认）：`{"code", "message", "data", "request_id"}`。

- `code`：字符串 slug（"OK" / "NOT_FOUND" / "VALIDATION_ERROR"…），**与 REST 状态码解耦**——
  HTTP 层走真状态码给网关/监控看，契约层 code 给前端分支看（§0.4 口径）。
- `request_id`：uuid4 hex，全链路追踪锚点（X-04 日志关联）。
"""

import uuid

from rest_framework.response import Response


def envelope(data=None, code="OK", message="success", request_id=None):
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": request_id or uuid.uuid4().hex,
    }


def ok(data=None, message="success", status=200):
    return Response(envelope(data=data, message=message), status=status)


def fail(code, message, status=400, data=None, request_id=None):
    return Response(
        envelope(data=data, code=code, message=message, request_id=request_id), status=status
    )
