#!/usr/bin/env python
from __future__ import annotations

import argparse
import select
import socketserver
import time
from pathlib import Path

import paramiko


class ForwardHandler(socketserver.BaseRequestHandler):
    transport = None
    remote_host = "127.0.0.1"
    remote_port = 5432

    def handle(self):
        try:
            channel = self.transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                self.request.getpeername(),
            )
        except Exception:
            return
        if channel is None:
            return
        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 30)
                if self.request in readable:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()
            self.request.close()


class ThreadingForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def load_connection_file(path: Path):
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    values = {}
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
    host = values.get("host") or lines[0].split(":", 1)[-1].strip()
    password = (
        values.get("password")
        or values.get("??")
        or lines[-1].split(":", 1)[-1].strip()
    )
    return host, "root", password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--connection-file",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".env",
    )
    parser.add_argument("--local-port", type=int, default=15432)
    parser.add_argument("--remote-host", default="172.19.0.2")
    parser.add_argument("--remote-port", type=int, default=5432)
    args = parser.parse_args()
    host, username, password = load_connection_file(args.connection_file)
    while True:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                host,
                username=username,
                password=password,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            transport = client.get_transport()
            transport.set_keepalive(30)
            ForwardHandler.transport = transport
            ForwardHandler.remote_host = args.remote_host
            ForwardHandler.remote_port = args.remote_port
            with ThreadingForwardServer(
                ("127.0.0.1", args.local_port), ForwardHandler
            ) as server:
                server.timeout = 5
                while transport.is_active():
                    server.handle_request()
        except Exception:
            time.sleep(5)
        finally:
            client.close()


if __name__ == "__main__":
    main()
