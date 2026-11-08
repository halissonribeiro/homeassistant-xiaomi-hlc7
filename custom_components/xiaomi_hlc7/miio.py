"""Minimal async miIO (UDP 54321) client with MIoT get/set/action helpers.

Protocol summary:
- Every packet has a 32-byte header: magic 0x2131, total length, 4 unknown
  bytes, device id, stamp, and a 16-byte checksum.
- A "hello" packet (header filled with 0xff, length 32) makes the device
  answer with its id and current stamp.
- Payloads are JSON encrypted with AES-128-CBC/PKCS7, key = md5(token),
  iv = md5(key + token). The checksum is md5(header-with-token + payload).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
import time
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

PORT = 54321
HELLO = bytes.fromhex("21310020" + "ff" * 28)


class MiioError(Exception):
    """Base miIO error."""


class MiioTimeout(MiioError):
    """The device did not answer."""


class MiioDeviceError(MiioError):
    """The device answered with an error object."""


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data).digest()  # noqa: S324 - protocol-defined


class _Codec:
    def __init__(self, token: bytes) -> None:
        self.token = token
        self.key = _md5(token)
        self.iv = _md5(self.key + token)

    def encrypt(self, plain: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        data = padder.update(plain) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return enc.update(data) + enc.finalize()

    def decrypt(self, cipher: bytes) -> bytes:
        dec = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        data = dec.update(cipher) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(data) + unpadder.finalize()

    def pack(self, did: int, stamp: int, payload: bytes) -> bytes:
        body = self.encrypt(payload)
        header = struct.pack(">HHIII", 0x2131, 32 + len(body), 0, did, stamp)
        checksum = _md5(header + self.token + body)
        return header + checksum + body

    def unpack(self, packet: bytes) -> tuple[int, int, bytes]:
        if len(packet) < 32:
            raise MiioError("short packet")
        magic, length, _unk, did, stamp = struct.unpack(">HHIII", packet[:16])
        if magic != 0x2131:
            raise MiioError("bad magic")
        body = packet[32:length]
        if not body:
            return did, stamp, b""
        if _md5(packet[:16] + self.token + body) != packet[16:32]:
            raise MiioError("bad checksum (wrong token?)")
        return did, stamp, self.decrypt(body)


class _Proto(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.queue.put_nowait(data)


class MiioClient:
    """One request at a time, re-handshakes on demand."""

    def __init__(self, host: str, token_hex: str, timeout: float = 5.0) -> None:
        self.host = host
        self._codec = _Codec(bytes.fromhex(token_hex))
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._did: int | None = None
        self._stamp_base = 0
        self._stamp_at = 0.0
        self._msg_id = int(time.time()) % 10000

    @property
    def did(self) -> int | None:
        return self._did

    async def _exchange(self, packet: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            _Proto, remote_addr=(self.host, PORT)
        )
        try:
            transport.sendto(packet)
            return await asyncio.wait_for(proto.queue.get(), self._timeout)
        except TimeoutError as err:
            raise MiioTimeout(f"no answer from {self.host}") from err
        finally:
            transport.close()

    async def _handshake(self) -> None:
        reply = await self._exchange(HELLO)
        _magic, _len, _unk, did, stamp = struct.unpack(">HHIII", reply[:16])
        self._did = did
        self._stamp_base = stamp
        self._stamp_at = time.monotonic()

    async def hello(self) -> int:
        """Handshake only (works without a valid token); returns the device id."""
        async with self._lock:
            await self._handshake()
        assert self._did is not None
        return self._did

    async def send(self, method: str, params: Any) -> Any:
        async with self._lock:
            for attempt in (1, 2):
                if self._did is None or attempt == 2:
                    await self._handshake()
                assert self._did is not None
                self._msg_id = (self._msg_id + 1) % 100000 or 1
                payload = json.dumps(
                    {"id": self._msg_id, "method": method, "params": params},
                    separators=(",", ":"),
                ).encode()
                stamp = self._stamp_base + int(time.monotonic() - self._stamp_at) + 1
                try:
                    raw = await self._exchange(
                        self._codec.pack(self._did, stamp, payload)
                    )
                    _did, _stamp, plain = self._codec.unpack(raw)
                except MiioTimeout:
                    if attempt == 2:
                        raise
                    continue
                msg = json.loads(plain.rstrip(b"\x00").decode())
                if "error" in msg:
                    raise MiioDeviceError(str(msg["error"]))
                return msg.get("result")
        raise MiioTimeout("unreachable")

    async def get_properties(
        self, props: list[tuple[int, int]]
    ) -> dict[tuple[int, int], Any]:
        """Read MIoT properties; values that fail are left out."""
        did = str(self._did or "x")
        out: dict[tuple[int, int], Any] = {}
        # Cameras choke on long requests: batch in groups of 8.
        for i in range(0, len(props), 8):
            chunk = props[i : i + 8]
            result = await self.send(
                "get_properties",
                [{"did": did, "siid": s, "piid": p} for s, p in chunk],
            )
            for item in result or []:
                if item.get("code") == 0:
                    out[(item["siid"], item["piid"])] = item.get("value")
        return out

    async def set_property(self, siid: int, piid: int, value: Any) -> None:
        did = str(self._did or "x")
        result = await self.send(
            "set_properties", [{"did": did, "siid": siid, "piid": piid, "value": value}]
        )
        code = (result or [{}])[0].get("code", 0)
        if code != 0:
            raise MiioDeviceError(f"set {siid}.{piid}={value!r} failed: code {code}")

    async def action(self, siid: int, aiid: int, args: list[Any] | None = None) -> Any:
        did = str(self._did or "x")
        result = await self.send(
            "action", {"did": did, "siid": siid, "aiid": aiid, "in": args or []}
        )
        if isinstance(result, dict) and result.get("code", 0) != 0:
            raise MiioDeviceError(f"action {siid}.{aiid} failed: {result}")
        return result
