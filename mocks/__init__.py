"""Mock 层（§6 第 1 条：自动化基座=真 HTTP 服务，进程内 mock 覆盖不到 Celery worker）。

- `gitlab_fake/`：假 GitLab（S0-6a，§4.6 端点表）
- `jenkins_fake/`：假 Jenkins（S0-6b，§3.8）
- 独立进程运行：`python -m mocks.gitlab_fake --port 8061`。
  **不属于生产镜像**（Dockerfile 不 COPY mocks/），业务代码零 `if mock` 分支（§6 第 2 条）。
"""
