#!/usr/bin/env python
"""Django 管理入口（settings 分片口径见 §0.6，默认 local）。"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django——确认 .venv 已激活（S0-3 骨架，requirements.txt）"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
