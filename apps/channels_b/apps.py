from django.apps import AppConfig


class ChannelsBConfig(AppConfig):
    name = "apps.channels_b"
    # label 缺省 "channels_b"——与 pip 包 channels 无冲突（§0.1）
