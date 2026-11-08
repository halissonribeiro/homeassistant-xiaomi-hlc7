"""Xiaomi MISS-over-CS2 P2P client (live video, audio and speaker).

Ported to Python from go2rtc (pkg/xiaomi/crypto, pkg/xiaomi/miss,
pkg/xiaomi/miss/cs2), Copyright (c) 2022 AlexxIT, MIT License.

Flow:
1. Generate a Curve25519 key pair and ask the cloud (miss_get_vendor) to
   sign our public key. The answer carries the camera's public key.
2. CS2 handshake on UDP 32108 straight to the camera's LAN IP.
3. Send the auth command with our public key + signature.
4. Every further command and every media payload is ChaCha20-encrypted
   with the NaCl box shared key (X25519 + HSalsa20).

Threads instead of asyncio so it runs the same in HA (executor) and in a
plain python3 shell. Only dependency: cryptography (bundled with HA).
"""

from __future__ import annotations

import json
import os
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

CODEC_H264 = 4
CODEC_H265 = 5
CODEC_PCM = 1024
CODEC_PCMU = 1026
CODEC_PCMA = 1027
CODEC_OPUS = 1032

CMD_AUTH_REQ = 0x100
CMD_VIDEO_START = 0x102
CMD_VIDEO_STOP = 0x103
CMD_AUDIO_START = 0x104
CMD_SPEAKER_START = 0x106
CMD_SPEAKER_STOP = 0x108
CMD_ENCODED = 0x1001

CS2_PORT = 32108
MAGIC = 0xF1
MAGIC_DRW = 0xD1
MSG_LAN_SEARCH = 0x30
MSG_PUNCH = 0x41
MSG_RDY_UDP = 0x42
MSG_RDY_TCP = 0x43
MSG_DRW = 0xD0
MSG_DRW_ACK = 0xD1
MSG_PING = 0xE0
MSG_PONG = 0xE1
MSG_CLOSE = 0xF0
HDR_SIZE = 32


class P2PError(Exception):
    """P2P connection failure."""


# ------------------------------------------------------------------ crypto
def generate_keypair() -> tuple[bytes, bytes]:
    """Return (public, private) raw 32-byte Curve25519 keys."""
    priv = X25519PrivateKey.generate()
    return (
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
        priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
    )


def _rotl(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _hsalsa20(key: bytes, nonce16: bytes) -> bytes:
    c = struct.unpack("<4I", b"expand 32-byte k")
    k = struct.unpack("<8I", key)
    n = struct.unpack("<4I", nonce16)
    x = [c[0], k[0], k[1], k[2], k[3], c[1], n[0], n[1],
         n[2], n[3], c[2], k[4], k[5], k[6], k[7], c[3]]  # fmt: skip

    def qr(a: int, b: int, cc: int, d: int) -> None:
        x[b] ^= _rotl((x[a] + x[d]) & 0xFFFFFFFF, 7)
        x[cc] ^= _rotl((x[b] + x[a]) & 0xFFFFFFFF, 9)
        x[d] ^= _rotl((x[cc] + x[b]) & 0xFFFFFFFF, 13)
        x[a] ^= _rotl((x[d] + x[cc]) & 0xFFFFFFFF, 18)

    for _ in range(10):
        qr(0, 4, 8, 12)
        qr(5, 9, 13, 1)
        qr(10, 14, 2, 6)
        qr(15, 3, 7, 11)
        qr(0, 1, 2, 3)
        qr(5, 6, 7, 4)
        qr(10, 11, 8, 9)
        qr(15, 12, 13, 14)
    return struct.pack("<8I", x[0], x[5], x[10], x[15], x[6], x[7], x[8], x[9])


def shared_key(device_public_hex: str, client_private: bytes) -> bytes:
    """NaCl crypto_box_beforenm: HSalsa20(X25519(priv, pub), 0)."""
    priv = X25519PrivateKey.from_private_bytes(client_private)
    pub = X25519PublicKey.from_public_bytes(bytes.fromhex(device_public_hex))
    return _hsalsa20(priv.exchange(pub), bytes(16))


def _chacha(key: bytes, nonce8: bytes, data: bytes) -> bytes:
    # Go's 12-byte nonce = 4 zero bytes + 8-byte nonce; counter starts at 0.
    nonce16 = bytes(4) + bytes(4) + nonce8
    return Cipher(algorithms.ChaCha20(key, nonce16), mode=None).encryptor().update(data)


def encode(data: bytes, key: bytes) -> bytes:
    nonce = os.urandom(8)
    return nonce + _chacha(key, nonce, data)


def decode(data: bytes, key: bytes) -> bytes:
    return _chacha(key, data[:8], data[8:])


# -------------------------------------------------------------- channels
class _Channel:
    """Reassembles a CS2 DRW channel: [size BE u32][payload] records."""

    def __init__(self, push_size: int, pop_size: int) -> None:
        self.wait_seq = 0
        self.push_buf: dict[int, bytes] = {}
        self.push_size = push_size
        self.wait_data = b""
        self.wait_size = 0
        self.q: queue.Queue[bytes | None] = queue.Queue(maxsize=pop_size)

    def push(self, b: bytes) -> None:
        self.wait_data += b
        while len(self.wait_data) > 4:
            if self.wait_size == 0:
                self.wait_size = struct.unpack(">I", self.wait_data[:4])[0]
                self.wait_data = self.wait_data[4:]
            if self.wait_size > len(self.wait_data):
                break
            try:
                self.q.put_nowait(self.wait_data[: self.wait_size])
            except queue.Full as err:
                raise P2PError("receive buffer full (consumer too slow)") from err
            self.wait_data = self.wait_data[self.wait_size :]
            self.wait_size = 0

    def push_seq(self, seq: int, data: bytes) -> int:
        diff = ((seq - self.wait_seq + 0x8000) & 0xFFFF) - 0x8000
        if diff > 0:
            if self.push_size == 0:
                return -1
            if seq not in self.push_buf:
                if len(self.push_buf) >= self.push_size:
                    return -1
                self.push_buf[seq] = data
            return 0
        if diff < 0:
            return 0
        i = 0
        while True:
            i += 1
            self.push(data)
            self.wait_seq = (self.wait_seq + 1) & 0xFFFF
            nxt = self.push_buf.pop(self.wait_seq, None)
            if nxt is None:
                return i
            data = nxt

    def close(self) -> None:
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass


@dataclass
class MediaPacket:
    codec: int
    sequence: int
    flags: int
    timestamp_ms: int
    payload: bytes

    @property
    def sample_rate(self) -> int:
        return 16000 if (self.flags >> 3) & 0xF else 8000


# ------------------------------------------------------------------ CS2
class Cs2Conn:
    """CS2 P2P transport over UDP (the hlc7 answers on UDP)."""

    def __init__(self, host: str, timeout: float = 5.0) -> None:
        self.host = host
        self.addr = (host, CS2_PORT)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.err: Exception | None = None
        self.trace: list[str] = []
        self._closed = False
        self._seq0 = 0
        self._seq3 = 0
        self._cmd_lock = threading.Lock()
        self._ack = threading.Event()
        self.channels = {0: _Channel(0, 10), 2: _Channel(250, 400)}
        self._handshake(timeout)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="cs2")
        self._thread.start()

    @property
    def remote(self) -> str:
        return f"{self.addr[0]}:{self.addr[1]}"

    def _write_until(self, req: bytes, ok, deadline: float) -> bytes:
        next_send = 0.0
        while True:
            now = time.monotonic()
            if now > deadline:
                seen = "; ".join(self.trace[-4:]) or "nothing received"
                raise P2PError(f"CS2 handshake timeout with {self.host} ({seen})")
            if now >= next_send:
                self.sock.sendto(req, self.addr)
                next_send = now + 1.0
            self.sock.settimeout(max(0.05, min(next_send, deadline) - now))
            try:
                res, addr = self.sock.recvfrom(1500)
            except TimeoutError:
                continue
            if addr[0] != self.host or len(res) < 16:
                continue
            self.trace.append(f"<- {res[1]:#04x} from port {addr[1]}")
            if ok(res):
                self.addr = (self.host, addr[1])
                return res

    def _handshake(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        res = self._write_until(
            bytes([MAGIC, MSG_LAN_SEARCH, 0, 0]), lambda r: r[1] == MSG_PUNCH, deadline
        )
        res = self._write_until(
            res, lambda r: r[1] in (MSG_RDY_UDP, MSG_RDY_TCP), deadline
        )
        if res[1] == MSG_RDY_TCP:
            raise P2PError("camera asked for CS2 over TCP (not implemented)")
        self.sock.settimeout(1.0)

    def _worker(self) -> None:
        try:
            while not self._closed:
                try:
                    buf, addr = self.sock.recvfrom(1500)
                except TimeoutError:
                    continue
                if addr[0] != self.host or len(buf) < 4:
                    continue
                kind = buf[1]
                if kind == MSG_DRW and len(buf) >= 8:
                    ch = buf[5]
                    channel = self.channels.get(ch)
                    if channel is None:
                        continue
                    pushed = channel.push_seq((buf[6] << 8) | buf[7], buf[8:])
                    if pushed >= 0:
                        self.sock.sendto(
                            bytes([MAGIC, MSG_DRW_ACK, 0, 6, MAGIC_DRW, ch, 0, 1, buf[6], buf[7]]),
                            self.addr,
                        )
                elif kind == MSG_PING:
                    self.sock.sendto(bytes([MAGIC, MSG_PONG, 0, 0]), self.addr)
                elif kind == MSG_DRW_ACK:
                    self._ack.set()
                elif kind == MSG_CLOSE:
                    raise P2PError("camera closed the session")
        except Exception as err:  # noqa: BLE001
            if not self._closed:
                self.err = err
        finally:
            for ch in self.channels.values():
                ch.close()

    def read_command(self, timeout: float = 5.0) -> tuple[int, bytes]:
        try:
            buf = self.channels[0].q.get(timeout=timeout)
        except queue.Empty as err:
            raise P2PError("no answer to command") from err
        if buf is None:
            raise P2PError(f"connection lost: {self.err}")
        return struct.unpack("<I", buf[:4])[0], buf[4:]

    def write_command(self, cmd: int, payload: bytes) -> None:
        with self._cmd_lock:
            size = len(payload)
            req = (
                bytes([MAGIC, MSG_DRW])
                + struct.pack(">H", 12 + size)
                + bytes([MAGIC_DRW, 0])
                + struct.pack(">H", self._seq0)
                + struct.pack(">I", 4 + size)
                + struct.pack(">I", cmd)
                + payload
            )
            self._seq0 = (self._seq0 + 1) & 0xFFFF
            for _ in range(5):
                self._ack.clear()
                self.sock.sendto(req, self.addr)
                if self._ack.wait(1.0):
                    return
            raise P2PError(f"camera did not ack command {cmd:#x}")

    def read_packet(self, timeout: float = 10.0) -> tuple[bytes, bytes]:
        try:
            data = self.channels[2].q.get(timeout=timeout)
        except queue.Empty as err:
            raise P2PError("no media for too long") from err
        if data is None:
            raise P2PError(f"connection lost: {self.err}")
        return data[:HDR_SIZE], data[HDR_SIZE:]

    def write_packet(self, hdr: bytes, payload: bytes) -> None:
        n = HDR_SIZE + len(payload)
        req = (
            bytes([MAGIC, MSG_DRW])
            + struct.pack(">H", n + 8)
            + bytes([MAGIC_DRW, 3])
            + struct.pack(">H", self._seq3)
            + struct.pack(">I", n)
            + hdr
            + payload
        )
        self._seq3 = (self._seq3 + 1) & 0xFFFF
        self.sock.sendto(req, self.addr)

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.sendto(bytes([MAGIC, MSG_CLOSE, 0, 0]), self.addr)
        except OSError:
            pass
        self.sock.close()


# ------------------------------------------------------------------ MISS
class MissClient:
    """Authenticated MISS session on top of CS2."""

    def __init__(
        self,
        host: str,
        client_public: bytes,
        client_private: bytes,
        device_public_hex: str,
        sign: str,
    ) -> None:
        self.key = shared_key(device_public_hex, client_private)
        self.conn = Cs2Conn(host)
        try:
            auth = json.dumps(
                {
                    "public_key": client_public.hex(),
                    "sign": sign,
                    "uuid": "",
                    "support_encrypt": 0,
                },
                separators=(",", ":"),
            ).encode()
            self.conn.write_command(CMD_AUTH_REQ, auth)
            _cmd, data = self.conn.read_command()
            if b'"result":"success"' not in data:
                raise P2PError(f"camera refused auth: {data[:200]!r}")
        except Exception:
            self.conn.close()
            raise

    @property
    def remote(self) -> str:
        return self.conn.remote

    def command(self, data: bytes) -> None:
        self.conn.write_command(CMD_ENCODED, encode(data, self.key))

    def start_media(self, quality: int = 2, audio: bool = True) -> None:
        """quality: 0 auto, 1 sd, 2 hd (3 breaks codec settings on some models)."""
        body = json.dumps(
            {"videoquality": quality, "enableaudio": int(audio)}, separators=(",", ":")
        ).encode()
        self.command(struct.pack(">I", CMD_VIDEO_START) + body)

    def stop_media(self) -> None:
        self.command(struct.pack(">I", CMD_VIDEO_STOP))

    def start_speaker(self) -> None:
        self.command(struct.pack(">I", CMD_SPEAKER_START))

    def stop_speaker(self) -> None:
        self.command(struct.pack(">I", CMD_SPEAKER_STOP))

    def read_packet(self, timeout: float = 10.0) -> MediaPacket:
        hdr, payload = self.conn.read_packet(timeout)
        _len, codec, seq, flags, ts = struct.unpack("<IIIIQ", hdr[:24])
        return MediaPacket(codec, seq, flags, ts, decode(payload, self.key))

    def write_audio(self, payload: bytes, codec: int = CODEC_PCMA) -> None:
        enc = encode(payload, self.key)
        hdr = struct.pack("<IIIIQ", len(enc), codec, 0, 0, int(time.time() * 1000))
        self.conn.write_packet(hdr + bytes(HDR_SIZE - 24), enc)

    def close(self) -> None:
        try:
            self.stop_media()
        except Exception:  # noqa: BLE001
            pass
        self.conn.close()


# ------------------------------------------------------------ G.711 A-law
_SEG_END = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)


def _alaw_byte(sample: int) -> int:
    """ITU G.711 A-law (Sun reference implementation)."""
    val = sample >> 3
    if val >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        val = -val - 1
    seg = next((i for i, end in enumerate(_SEG_END) if val <= end), 8)
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << 4
    aval |= (val >> 1) & 0x0F if seg < 2 else (val >> seg) & 0x0F
    return aval ^ mask


def pcm16_to_alaw(pcm: bytes) -> bytes:
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2])
    return bytes(_alaw_byte(s) for s in samples)
