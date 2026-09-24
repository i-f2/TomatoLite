"""统一分页（S0-4 拍板默认 page_size=20）：data = {items, page, page_size, total}，外套 envelope。"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .response import envelope


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200  # 放开无界 page_size 的护栏（S1 起清单/日志类接口按需单独子类）

    def get_paginated_response(self, data):
        return Response(envelope(data={
            "items": data,
            "page": self.page.number,
            "page_size": self.get_page_size(self.request),
            "total": self.page.paginator.count,
        }))
