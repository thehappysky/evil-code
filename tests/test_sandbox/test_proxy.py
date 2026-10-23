# [Phase 7] 2026-08-05: Tests for egress allowlist proxy.
"""Tests for code_agent/sandbox/proxy.py."""

from __future__ import annotations

import asyncio

import pytest

from code_agent.sandbox.proxy import EgressProxy, _allowed


def test_allowlist_matching():
    assert _allowed("pypi.org", {"pypi.org"})
    assert _allowed("files.pythonhosted.org", {"pythonhosted.org"})
    assert _allowed("sub.github.com", {"github.com"})
    assert not _allowed("evil.com", {"example.com"})
    assert not _allowed("evilexample.com", {"example.com"})
    assert not _allowed("", {"example.com"})


@pytest.mark.asyncio
async def test_connect_to_disallowed_host_gets_403():
    proxy = EgressProxy(allowlist={"example.com"})
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT blocked.invalid:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        status = await reader.readline()
        assert b"403" in status
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_connect_tunnel_to_localhost():
    """白名单含 localhost：CONNECT 隧道能连通本地 echo 服务。"""
    async def echo_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo_server, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]

    proxy = EgressProxy(allowlist={"localhost"})
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(f"CONNECT localhost:{upstream_port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        status = await reader.readline()
        assert b"200" in status
        # CONNECT 头部结束后还有一个空行，先消费掉
        blank = await reader.readline()
        assert blank in (b"\r\n", b"\n")
        # 隧道建立后双向转发
        writer.write(b"ping")
        await writer.drain()
        echoed = await asyncio.wait_for(reader.read(4), timeout=5)
        assert echoed == b"ping"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_absolute_uri_http_relay():
    """absolute-URI 形式的 HTTP 请求经白名单代理转发。"""
    async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readline()  # request line
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(http_handler, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]

    proxy = EgressProxy(allowlist={"localhost"})
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            f"GET http://localhost:{upstream_port}/x HTTP/1.1\r\n\r\n".encode()
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=5)
        assert b"200 OK" in response
        assert b"hello" in response
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()
