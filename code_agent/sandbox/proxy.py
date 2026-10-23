# [Phase 7] 2026-08-05: 出站白名单过滤代理（CONNECT 隧道 + HTTP absolute-URI）。
"""Egress allowlist proxy for sandbox containers.

沙箱的 net/work 档容器通过 ``HTTP_PROXY`` 等环境变量把出站流量导向本代理，
代理只放行白名单域名，其他请求一律 403。

支持两种代理流量：
- ``CONNECT host:port``（HTTPS 隧道，pip/curl/git 主要走这个）
- ``GET http://host/path HTTP/1.1`` 形式的 absolute-URI（普通 HTTP）

诚实边界：只约束走代理的工具；绕过代理直连的裸 socket 工具拦不住。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


DEFAULT_EGRESS_ALLOWLIST: frozenset[str] = frozenset({
    # Python 包
    "pypi.org",
    "files.pythonhosted.org",
    # GitHub
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "githubusercontent.com",
    # npm / Node
    "registry.npmjs.org",
    "npmjs.org",
    "nodejs.org",
    # 其他常用
    "example.com",
    "localhost",
})


def _allowed(host: str, allowlist: Iterable[str]) -> bool:
    host = host.lower().strip().rstrip(".")
    if not host:
        return False
    return any(
        host == name or host.endswith("." + name)
        for name in allowlist
    )


class EgressProxy:
    """基于 asyncio 的轻量过滤代理。"""

    def __init__(
        self,
        allowlist: Iterable[str] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        self._allowlist = {
            name.lower().strip().rstrip(".")
            for name in (allowlist or DEFAULT_EGRESS_ALLOWLIST)
            if name and name != "*"
        }
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._task: asyncio.Task | None = None

    @property
    def port(self) -> int:
        if self._server is None or self._server.sockets is None:
            return self._port
        return self._server.sockets[0].getsockname()[1]

    @property
    def proxy_url(self) -> str:
        # 容器内通过 host.docker.internal 访问宿主（监听面仍是 self._host）
        return f"http://host.docker.internal:{self.port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
            limit=65536,
        )
        self._task = asyncio.create_task(self._server.serve_forever())
        logger.info("Egress proxy listening on %s", self.proxy_url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ── 连接处理 ────────────────────────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return
            request_line = line.decode("utf-8", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 2:
                await self._reject(writer, "bad_request")
                return

            method, target = parts[0].upper(), parts[1]
            if method == "CONNECT":
                host, _, port = target.partition(":")
                if not _allowed(host, self._allowlist):
                    logger.warning(
                        "Egress denied CONNECT to %s (peer %s)", target, peer
                    )
                    await self._reject(writer, "domain_not_allowed")
                    return
                # 消费 CONNECT 剩余请求头（Host 等），避免泄漏进隧道数据
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=30)
                    if not line or line in (b"\r\n", b"\n"):
                        break
                await self._tunnel(reader, writer, host, int(port or 443))
                return

            # absolute-URI 形式的普通 HTTP（GET http://host/path ...）
            if target.lower().startswith("http://"):
                host, _, rest = target[7:].partition("/")
                hostname, _, port = host.partition(":")
                if not _allowed(hostname, self._allowlist):
                    logger.warning(
                        "Egress denied HTTP %s %s (peer %s)",
                        method, target, peer,
                    )
                    await self._reject(writer, "domain_not_allowed")
                    return
                await self._relay_http(
                    reader, writer, hostname, int(port or 80), request_line
                )
                return

            await self._reject(writer, "unsupported_request")
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _reject(self, writer: asyncio.StreamWriter, reason: str) -> None:
        body = f"Egress blocked by code-agent sandbox policy ({reason})".encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=30
            )
        except (OSError, asyncio.TimeoutError) as exc:
            await self._reject(client_writer, f"upstream_error:{exc}")
            return
        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        await self._pump_both(
            client_reader, upstream_writer,
            upstream_reader, client_writer,
        )

    async def _relay_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        host: str,
        port: int,
        first_line: str,
    ) -> None:
        """把 absolute-URI 请求改写成 origin-form 后转发。"""
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=30
            )
        except (OSError, asyncio.TimeoutError) as exc:
            await self._reject(client_writer, f"upstream_error:{exc}")
            return

        # 收集剩余请求头
        header_lines = [first_line]
        has_host = False
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=30)
            if not line or line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded.lower().startswith("host:"):
                has_host = True
            if decoded.lower().startswith(("proxy-connection", "proxy-authorization")):
                continue
            header_lines.append(decoded)

        # absolute-URI → origin-form
        parts = first_line.split()
        if len(parts) == 3:
            method, target, version = parts
            path = target[7:].partition("/")[2] or "/"
            header_lines[0] = f"{method} /{path} {version}"
        if not has_host:
            header_lines.append(f"Host: {host}:{port}")

        payload = ("\r\n".join(header_lines) + "\r\n\r\n").encode()
        upstream_writer.write(payload)
        await upstream_writer.drain()

        await self._pump_both(
            client_reader, upstream_writer,
            upstream_reader, client_writer,
        )

    @staticmethod
    async def _pump_both(
        a_reader: asyncio.StreamReader,
        a_writer: asyncio.StreamWriter,
        b_reader: asyncio.StreamReader,
        b_writer: asyncio.StreamWriter,
    ) -> None:
        """双向泵：任一端结束立即取消另一端并关闭两个 writer。"""

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError, asyncio.CancelledError):
                pass

        task_a = asyncio.create_task(pump(a_reader, a_writer))
        task_b = asyncio.create_task(pump(b_reader, b_writer))
        try:
            done, pending = await asyncio.wait(
                {task_a, task_b}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    await task
                except (ConnectionError, OSError):
                    pass
        finally:
            for writer in (a_writer, b_writer):
                try:
                    writer.close()
                except Exception:
                    pass


__all__ = [
    "DEFAULT_EGRESS_ALLOWLIST",
    "EgressProxy",
]
