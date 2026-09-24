"""独立进程入口（与 8061/8062 错开；8063 刻意弃用——6b 冒烟的一次性接收器）：

    python -m mocks.host_fake    # app 8064 / tcp 8065 / ssh 8066 / control 8067

密钥引导进 `var/mockhost/`（`.gitignore` 已含 `var/`）：主机密钥 + 客户端密钥对
各生成一次、重启不换脸；公钥即 `authorized_keys`。Step 4 冒烟用**真 OpenSSH
客户端**连这里，证明协议面不是 asyncssh 自说自话。
"""

import argparse
import os
import threading

from .server import HostSim


def main() -> None:
    ap = argparse.ArgumentParser(prog="mocks.host_fake")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--app-port", type=int, default=8064)
    ap.add_argument("--tcp-port", type=int, default=8065)
    ap.add_argument("--ssh-port", type=int, default=8066)
    ap.add_argument("--control-port", type=int, default=8067)
    ap.add_argument("--key-dir", default="var/mockhost")
    args = ap.parse_args()

    from .ssh import gen_key, write_authorized_keys

    gen_key(os.path.join(args.key_dir, "host_ed25519"))  # 主机密钥文件是目的；服务端按路径加载
    client_key = gen_key(os.path.join(args.key_dir, "client_ed25519"))
    authk = os.path.join(args.key_dir, "authorized_keys")
    write_authorized_keys([client_key], authk)

    hs = HostSim(
        args.host,
        args.app_port,
        args.tcp_port,
        args.control_port,
        ssh_authorized_keys_path=authk,
        ssh_host_key_path=os.path.join(args.key_dir, "host_ed25519"),
        ssh_port=args.ssh_port,
    )
    print(
        f"MOCK-HOST app=http://127.0.0.1:{hs.app_port} "
        f"tcp=127.0.0.1:{hs.tcp_port} "
        f"ssh=127.0.0.1:{hs.ssh.port} (-i {args.key_dir}/client_ed25519) "
        f"control=http://127.0.0.1:{hs.control_port}",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


main()
