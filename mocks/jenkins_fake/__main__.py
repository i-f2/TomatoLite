"""独立进程入口（拍板①：主机进程，不进 compose；端口与 gitlab_fake 8061 错开）：

python -m mocks.jenkins_fake --port 8062
"""

import argparse

from .server import make_server


def main() -> None:
    ap = argparse.ArgumentParser(prog="mocks.jenkins_fake")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8062)
    args = ap.parse_args()
    httpd, _ = make_server(args.host, args.port)
    print(f"MOCK-JENKINS LISTENING http://{args.host}:{httpd.server_address[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


main()
