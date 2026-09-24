"""独立进程入口（拍板①：主机进程，不进 compose）：

python -m mocks.gitlab_fake --port 8061
"""

import argparse

from .server import make_server


def main() -> None:
    ap = argparse.ArgumentParser(prog="mocks.gitlab_fake")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8061)
    args = ap.parse_args()
    httpd, _ = make_server(args.host, args.port)
    print(f"MOCK-GITLAB LISTENING http://{args.host}:{httpd.server_address[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


main()
