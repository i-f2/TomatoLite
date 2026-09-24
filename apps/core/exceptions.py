"""统一异常 → envelope（挂 settings REST_FRAMEWORK.EXCEPTION_HANDLER）。

口径：AppError 自带 code_slug + HTTP status；DRF 原生异常按**异常类名**转 slug
（NotFound → NOT_FOUND、ValidationError → VALIDATION_ERROR——default_code 多为泛化
"invalid"，弃用），HTTP 状态码透传。非 API 异常返回 None 走 Django 500——
**不吞、不伪装成 envelope**（§0.7 立场：错误可见）。
"""

import re

from rest_framework import status as http_status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler

from .response import envelope


class AppError(APIException):
    """业务异常基类：子类或实例化时给 code（slug）/ message / status_code。"""

    status_code = http_status.HTTP_400_BAD_REQUEST
    code_slug = "APP_ERROR"

    def __init__(self, message="app error", code=None, status_code=None):
        if code is not None:
            self.code_slug = code
        if status_code is not None:
            self.status_code = status_code
        self.message = message
        super().__init__(message)


def _slug(cls_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).upper()


def _drf_message(response):
    data = response.data
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return str(data)


def app_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # 非 DRF 异常：交 Django，不套 envelope
    if isinstance(exc, AppError):
        code, message = exc.code_slug, exc.message
    else:
        code = _slug(type(exc).__name__)
        message = _drf_message(response)
    response.data = envelope(data=None, code=code, message=message)
    return response
