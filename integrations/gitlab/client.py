"""GitLab httpx 客户端占位（实现归 M1-13/M2-03；本文件只钉形状，全部 NotImplementedError）。

三态（mock/integration/prod，§0.6）差异只在 base_url——构造参数进、分支不出（§0.2 规则 2）。
"""
from __future__ import annotations

import httpx

from .interface import GitLabProvider


class GitLabClient(GitLabProvider):
    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
        )

    def get_file(self, project, path, ref):  # noqa: ANN001,ANN001-003
        raise NotImplementedError("M1-13")

    def create_commit(self, project, branch, message, actions):  # noqa: ANN001
        raise NotImplementedError("M1-13")

    def list_commits(self, project, path, since, per_page=20):  # noqa: ANN001
        raise NotImplementedError("M1-13")
