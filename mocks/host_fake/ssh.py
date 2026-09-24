"""真 SSH-2 服务端（asyncssh——§5.2 `SshTransport` 设计钦定件，本任务只做**被连方**）。

为什么必须是真协议（§6 第 1/7 条推论）：`SshTransport` 跑在 Celery worker 里，
进程内假对象覆盖不了 socket 与协议层；客户端 `if mock` 分支违 §6 第 2 条。

exec 解释器 = **Linux/GNU coreutils 方言**（Spec 拍板③，六命令）：mock 说的是
"目标机"的语言（生产目标全是 Linux），不是本机 macOS 的——`stat -c`、
`systemctl is-active` exit 3、未知命令 exit 127（§5.3 "命令不存在" ERROR 素材）。
纯函数 `run_command()` 与线路层分离：方言契约可脱离 SSH 直接断言。

publickey-only 的实现真相：asyncssh 服务端对 password 请求只会**立刻拒绝**、
绝不交互式发问——BatchMode 客户端（§1132 首坑：密码提示挂到超时）在本 mock
上不可能挂住；错钥被拒由测试断"快"而不只是"拒"。
"""

from __future__ import annotations

import asyncio
import os
import shlex
import threading

import asyncssh

from .state import State

APP_CMDLINE = "/usr/local/bin/tlapp --config /etc/tlapp.yaml --detached"
APP_UNIT = "tlapp"
APP_PID = 4001

_NO_FILE = "No such file or directory"
_NO_PROC = "No such process"


def _within_dir(state: State, path: str) -> str | None:
    """只服务 state.dir 之下的文件；越界路径=不存在（不泄露本机文件系统）。"""
    real = os.path.realpath(os.path.join(state.dir, path))
    return real if real.startswith(os.path.realpath(state.dir) + os.sep) else None


def run_command(state: State, argv: list[str]) -> tuple[int, str, str]:
    """六命令 coreutils 方言解释器。返回 (exit_status, stdout, stderr)。"""
    with state.lock:
        alive = state.config["process_alive"]
        if not argv:
            return 0, "", ""
        cmd, args = argv[0], argv[1:]

        if cmd == "pgrep":
            if args and args[0] == "-f":
                pat = args[1] if len(args) > 1 else ""
            elif args:
                pat = args[0]
            else:
                return 2, "", "usage: pgrep [-f] pattern\n"
            if alive and pat and pat in APP_CMDLINE:
                return 0, f"{APP_PID}\n", ""
            return 1, "", ""  # 真 pgrep：查无此进程=退出码 1，无 stderr

        if cmd == "systemctl" and args[:1] == ["is-active"]:
            if alive and len(args) > 1 and args[1] == APP_UNIT:
                return 0, "active\n", ""
            return 3, "inactive\n", ""  # systemd 惯例：非 active 一律 exit 3

        if cmd == "kill":
            if args[:1] != ["-0"] or len(args) != 2:
                return 2, "", "kill: usage: kill [-s sigspec | -n signum | -sigspec] pid\n"
            try:
                pid = int(args[1])
            except ValueError:
                return 2, "", f"kill: {args[1]}: argument expected\n"
            if alive and pid == APP_PID:
                return 0, "", ""
            return 1, "", f"kill: ({pid}) - {_NO_PROC}\n"

        if cmd == "cat" and len(args) == 1:
            real = _within_dir(state, args[0])
            if real is None or not os.path.exists(real):
                return 1, "", f"cat: {args[0]}: {_NO_FILE}\n"
            with open(real) as f:
                return 0, f.read(), ""

        if cmd == "tail" and args[:1] == ["-n"] and len(args) == 3:
            try:
                n = int(args[1])
            except ValueError:
                return 1, "", "tail: option requires an integer -- 'n'\n"
            real = _within_dir(state, args[2])
            if real is None or not os.path.exists(real):
                return 1, "", f"tail: cannot open '{args[2]}' for reading: {_NO_FILE}\n"
            with open(real) as f:
                lines = f.readlines()
            return 0, "".join(lines[-n:]), ""

        if cmd == "stat" and args[:1] == ["-c"] and len(args) == 3:
            real = _within_dir(state, args[2])
            if real is None or not os.path.exists(real):
                return 1, "", f"stat: cannot statx '{args[2]}': {_NO_FILE}\n"
            st = os.stat(real)
            out = (
                args[1]
                .replace("%i", str(st.st_ino))
                .replace("%s", str(st.st_size))
                .replace("%n", real)
            )
            return 0, out + "\n", ""

        return 127, "", f"bash: line 1: {cmd}: command not found\n"


class _ExecSession(asyncssh.SSHServerSession):
    def __init__(self, state: State):
        self._state = state
        self.chan = None
        self._argv: list[str] = []

    def connection_made(self, chan):
        self.chan = chan

    def exec_requested(self, command: str) -> bool:  # asyncssh classic 会话钩子
        try:
            self._argv = shlex.split(command or "")
        except ValueError:
            self._argv = []
        return True  # 只放行；契约明写 session_started 前不得发输出

    def session_started(self) -> None:
        # 通道完全打开后才执行并回写；exit() 自带"发 exit-status + 关通道"（2.24 文档）
        rc, out, err = run_command(self._state, self._argv or [])
        if out:
            self.chan.write(out)
        if err:
            self.chan.write_stderr(err)
        self.chan.exit(rc)


class _Server(asyncssh.SSHServer):
    def __init__(self, state: State):
        self._state = state

    def begin_auth(self, username: str) -> bool:
        return True  # 永远要求认证，无匿名通道

    def session_requested(self):
        return _ExecSession(self._state)


class SshEndpoint:
    """独立事件循环线程上跑 asyncssh listen（与 http.server 线程群并存于一个进程）。"""

    def __init__(
        self,
        state: State,
        addr: str = "127.0.0.1",
        port: int = 0,
        authorized_keys_path: str = "",
        host_key_path: str = "",
    ):
        self._state = state
        self._addr = addr
        self._bind_port = port
        self._authorized_keys_path = authorized_keys_path
        self._host_key_path = host_key_path
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.AbstractServer | None = None
        self._err: BaseException | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        if self._err:
            raise self._err

    async def _serve(self):
        state = self._state
        return await asyncssh.create_server(
            lambda: _Server(state),
            self._addr,
            self._bind_port,
            server_host_keys=[self._host_key_path],
            authorized_client_keys=self._authorized_keys_path,
        )

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._server = self._loop.run_until_complete(self._serve())
        except BaseException as e:  # noqa: BLE001 —— 起不来必须让构造方立刻知道
            self._err = e
        finally:
            self._ready.set()
        if self._server is not None:
            self._loop.run_forever()

    @property
    def port(self) -> int:
        socks = self._server.sockets if self._server else ()
        return socks[0].getsockname()[1] if socks else 0

    def close(self) -> None:
        try:
            if self._server:
                self._loop.call_soon_threadsafe(self._server.close)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=3)
        finally:
            self._loop.close()


def gen_key(path: str) -> asyncssh.SSHKey:
    """生成/加载 ed25519 私钥文件（0600）。已存在则加载——独立进程重启不换脸。"""
    if os.path.exists(path):
        return asyncssh.import_private_key(open(path).read())
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    key = asyncssh.generate_private_key("ssh-ed25519", comment="mockhost")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.export_private_key().decode())
    return key


def write_authorized_keys(pub_keys: list[asyncssh.SSHKey], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        for k in pub_keys:
            f.write(k.export_public_key().decode() + "\n")
