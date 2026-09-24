"""Mock 目标主机：app HTTP 端口 + 裸 TCP 端口 + 控制端口（Step 2 追加 SSH）。

两条硬设计（均为 §6 纪律的推论）：
1. **控制端口独立**——`refuse`/`blackhole` 会把 app 端口整张脸弄死，控制面若与
   app 同端口就关不回去（"开关关得回去"在 mock 主机上是端口级前提，不是路径级）。
2. **`refuse` 必须真关听**（listener socket 关闭→内核回 RST），黑洞必须真收了
   不回；TIMEOUT_CONNECT（SYN 无应答）本机内核 backlog 做不到，由消费方测试用
   RFC5737 `192.0.2.1` 构造（Spec 拍板③）——本 mock 只造另两形，三者互不混淆。

Jenkins/GitLab mock 的同族纪律：config 逐键校验（坏值 400 零写入）、reset 连
配置、`_control` 面永不被注入、响应写包 `except OSError`。
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .state import DEFAULT_CONFIG, State

_VALIDATORS = {
    "http_status": lambda v: isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599,
    "http_body": lambda v: isinstance(v, str),
    "http_mode": lambda v: v in ("normal", "refuse", "blackhole"),
    "tcp_listen": lambda v: isinstance(v, bool),
    "version": lambda v: isinstance(v, str) and v != "",
    "process_alive": lambda v: isinstance(v, bool),
}


def _silent(cls):
    cls.log_message = lambda self, *a: None
    return cls


@_silent
class _AppHandler(BaseHTTPRequestHandler):
    """被探活的"应用"：GET 任意路径=配置的码+体；/version=文件。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._read_body()
        self._serve()

    def _read_body(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)

    def _serve(self) -> None:
        st: State = self.server.host.state
        with st.lock:
            mode = st.effective_app_mode()
            path = self.path.partition("?")[0]
            if path == "/version":
                status, body, ctype = 200, json.dumps({"version": st.get_version()}).encode(), "application/json"
            else:
                status = st.config["http_status"]
                body = st.config["http_body"].encode()
                ctype = "text/plain; charset=utf-8"
        if mode == "blackhole":
            # 真黑洞：一个字节都不回，直到对端放弃（recv 返回 b''/抛错才放线程）
            try:
                while self.connection.recv(4096):
                    pass
            except OSError:
                pass
            return
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass


@_silent
class _ControlHandler(BaseHTTPRequestHandler):
    """控制面：config 逐键校验、日志三动作、reset、info。永不响应应用探测语义。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = self.path.partition("?")[0]
        host: HostSim = self.server.host
        if path == "/_control/config":
            with host.state.lock:
                self._json(200, dict(host.state.config))
        elif path == "/_control/info":
            self._json(200, host.info())
        elif path == "/_control/log":
            self._json(200, {"text": host.state.read_log()})
        else:
            self._json(404, {"message": "no such control endpoint"})

    def do_POST(self) -> None:
        raw = self._read_body()
        path = self.path.partition("?")[0]
        host: HostSim = self.server.host
        if path == "/_control/config":
            self._post_config(raw)
        elif path == "/_control/reset":
            host.state.reset()
            host.apply_app()
            host.apply_tcp()
            self._json(200, {"ok": True})
        elif path in ("/_control/log/append", "/_control/log/lines"):
            body = self._json_body(raw)
            lines = body.get("lines") if isinstance(body, dict) else None
            if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
                self._json(400, {"message": "lines: list[str] required"})
                return
            if path.endswith("append"):
                host.state.append_log(lines)
            else:
                host.state.set_log(lines)
            self._json(200, {"ok": True, "line_count": len(lines)})
        elif path == "/_control/log/rotate":
            host.state.rotate_log()
            st = host.state.log_stat()
            self._json(200, {"ok": True, "inode": st.st_ino, "size": st.st_size})
        else:
            self._json(404, {"message": "no such control endpoint"})

    # ---- helpers ----

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    @staticmethod
    def _json_body(raw: bytes):
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return None

    def _post_config(self, raw: bytes) -> None:
        host: HostSim = self.server.host
        body = self._json_body(raw)
        if body is None or not isinstance(body, dict):
            self._json(400, {"message": "json object required"})
            return
        unknown = sorted(set(body) - set(DEFAULT_CONFIG))
        if unknown:
            self._json(400, {"message": f"unknown config keys: {unknown}"})
            return
        bad = [k for k, v in body.items() if not _VALIDATORS[k](v)]
        if bad:
            self._json(400, {"message": f"bad values for: {sorted(bad)}"})
            return
        with host.state.lock:
            host.state.config.update(body)
            if "version" in body:
                host.state._write_version(body["version"])
        host.apply_app()   # 幂等；http_mode/process_alive 变化在此兑现到 listener
        host.apply_tcp()
        with host.state.lock:
            self._json(200, dict(host.state.config))

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass


def _serve(httpd: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


class HostSim:
    """一台假主机 = 三个面（Step 2 起四个）+ 临时目录里的真日志文件。

    端口 0=自动分配；`refuse` 关闭/恢复 app listener 时**端口号恒定**（探活
    URL 按端口钉死，换端口=谎报另一台机器）。
    """

    def __init__(self, addr: str = "127.0.0.1", app_port: int = 0,
                 tcp_port: int = 0, control_port: int = 0,
                 state: State | None = None,
                 ssh_authorized_keys_path: str | None = None,
                 ssh_host_key_path: str | None = None,
                 ssh_port: int = 0):
        self.addr = addr
        self.state = state or State()
        self.ssh = None
        self._app: ThreadingHTTPServer | None = None
        self._app_thread: threading.Thread | None = None
        self.app_port = app_port
        self.tcp_port = tcp_port
        self._tcp_sock: socket.socket | None = None
        self._ctl = ThreadingHTTPServer((addr, control_port), _ControlHandler)
        self._ctl.daemon_threads = True
        self._ctl.host = self
        self.control_port = self._ctl.server_address[1]
        self._ctl_thread = _serve(self._ctl)
        if ssh_authorized_keys_path:
            from .ssh import SshEndpoint       # 延迟 import：不开 SSH 的用法不拖 asyncssh
            self.ssh = SshEndpoint(self.state, addr, ssh_port,
                                   ssh_authorized_keys_path, ssh_host_key_path)
        self.apply_app()
        self.apply_tcp()

    # ---- 表面 ----

    @property
    def app_url(self) -> str:
        return f"http://{self.addr}:{self.app_port}"

    @property
    def control_url(self) -> str:
        return f"http://{self.addr}:{self.control_port}"

    def info(self) -> dict:
        with self.state.lock:
            eff = self.state.effective_app_mode()
        return {"app_port": self.app_port, "tcp_port": self.tcp_port,
                "control_port": self.control_port,
                "ssh_port": self.ssh.port if self.ssh else None,
                "app_listening": self._app is not None, "effective_app_mode": eff,
                "log_path": self.state.log_path, "log_rotated_path": self.state.log_rotated_path,
                "version_path": self.state.version_path,
                "process_alive": self.state.config["process_alive"]}

    # ---- 联动面 ----

    def apply_app(self) -> None:
        want_listening = self.state.effective_app_mode() != "refuse"
        if want_listening and self._app is None:
            httpd = ThreadingHTTPServer((self.addr, self.app_port), _AppHandler)
            httpd.daemon_threads = True
            httpd.host = self
            self.app_port = httpd.server_address[1]
            self._app = httpd
            self._app_thread = _serve(httpd)
        elif not want_listening and self._app is not None:
            httpd, thread = self._app, self._app_thread
            self._app, self._app_thread = None, None
            httpd.shutdown()          # 让 serve_forever 线程干净退出（poll 0.5s）
            thread.join(timeout=3)
            httpd.server_close()      # listener 关闭→后续连接内核回 RST

    def apply_tcp(self) -> None:
        want = self.state.config["tcp_listen"]
        if want and self._tcp_sock is None:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.addr, self.tcp_port))
            s.listen(16)
            self.tcp_port = s.getsockname()[1]
            self._tcp_sock = s
            threading.Thread(target=self._tcp_loop, args=(s,), daemon=True).start()
        elif not want and self._tcp_sock is not None:
            s, self._tcp_sock = self._tcp_sock, None
            s.close()                 # accept 线程收 OSError 自灭

    @staticmethod
    def _tcp_loop(s: socket.socket) -> None:
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                break
            conn.close()              # "端口开"=握手即接受即关，无应用语义

    def close(self) -> None:
        if self.ssh:
            self.ssh.close()
        for httpd, thread in ((self._app, self._app_thread), (self._ctl, self._ctl_thread)):
            if httpd is not None:
                httpd.shutdown()
                thread.join(timeout=3)
                httpd.server_close()
        self._app, self._app_thread = None, None
        if self._tcp_sock is not None:
            self._tcp_sock.close()
            self._tcp_sock = None
        self.state.cleanup()
