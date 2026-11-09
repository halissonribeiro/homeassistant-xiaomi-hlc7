"""Tiny local RTSP server (TCP interleaved) for one camera.

Serves the camera's own H.265/H.264 + PCMA without transcoding, so Home
Assistant (its built-in go2rtc / stream component) can pick it up through
camera.stream_source(). Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import struct
import time

from .streamer import CameraStreamer, split_nals
from .xiaomi_p2p import CODEC_H264, CODEC_H265, CODEC_PCMA, MediaPacket

_LOGGER = logging.getLogger(__name__)

MAX_RTP_PAYLOAD = 1400
PT_VIDEO = 96
PT_PCMA = 8
READY_TIMEOUT_S = 20
QUEUE_PACKETS = 600


class _Session:
    """One PLAYing RTSP client."""

    def __init__(self, writer: asyncio.StreamWriter, loop: asyncio.AbstractEventLoop) -> None:
        self.writer = writer
        self.loop = loop
        self.channels: dict[str, int] = {}  # "video"/"audio" -> interleaved channel
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(QUEUE_PACKETS)
        self.v_seq = random.randint(0, 0xFFFF)
        self.a_seq = random.randint(0, 0xFFFF)
        self.v_ssrc = random.getrandbits(32)
        self.a_ssrc = random.getrandbits(32)
        self.a_ts = random.getrandbits(32)
        self.v_base = random.getrandbits(32)
        self.v_ts0: int | None = None
        self.got_keyframe = False
        self.consumer_id: int | None = None
        self.v_count = self.v_octets = self.a_count = self.a_octets = 0
        self.v_last_ts = 0
        self.last_sr = 0.0

    def _sender_reports(self) -> list[bytes]:
        """RTCP SR per track (~1/s) so clients can place/sync timestamps."""
        now = time.time()
        if now - self.last_sr < 1.0 or self.v_ts0 is None:
            return []
        self.last_sr = now
        ntp = int((now + 2208988800) * (1 << 32))
        out = []
        for kind, ssrc, ts, cnt, octets in (
            ("video", self.v_ssrc, self.v_last_ts, self.v_count, self.v_octets),
            ("audio", self.a_ssrc, self.a_ts, self.a_count, self.a_octets),
        ):
            if kind not in self.channels:
                continue
            sr = struct.pack(
                ">BBHIQIII", 0x80, 200, 6, ssrc, ntp & 0xFFFFFFFFFFFFFFFF,
                ts & 0xFFFFFFFF, cnt & 0xFFFFFFFF, octets & 0xFFFFFFFF,
            )
            out.append(struct.pack(">cBH", b"$", self.channels[kind] + 1, len(sr)) + sr)
        return out

    # Called from the streamer thread.
    def on_packet(self, pkt: MediaPacket) -> None:
        frames = self._packetize(pkt)
        if frames:
            self.loop.call_soon_threadsafe(self._enqueue, frames)

    def _enqueue(self, frames: list[bytes]) -> None:
        for frame in frames:
            try:
                self.queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Client too slow: drop until the next keyframe.
                self.got_keyframe = False
                return

    def _rtp(self, channel: int, pt: int, seq: int, ts: int, ssrc: int, marker: bool, payload: bytes) -> bytes:
        hdr = struct.pack(
            ">BBHII", 0x80, (0x80 if marker else 0) | pt, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc
        )
        body = hdr + payload
        return struct.pack(">cBH", b"$", channel, len(body)) + body

    def _packetize(self, pkt: MediaPacket) -> list[bytes]:
        out: list[bytes] = []
        if pkt.codec in (CODEC_H264, CODEC_H265) and "video" in self.channels:
            nals = split_nals(pkt.payload)
            if not nals:
                return out
            h265 = pkt.codec == CODEC_H265
            if not self.got_keyframe:
                first = (nals[0][0] >> 1) & 0x3F if h265 else nals[0][0] & 0x1F
                if first != (32 if h265 else 7):
                    return out
                self.got_keyframe = True
            if self.v_ts0 is None:
                self.v_ts0 = pkt.timestamp_ms
            ts = (self.v_base + (pkt.timestamp_ms - self.v_ts0) * 90) & 0xFFFFFFFF
            self.v_last_ts = ts
            ch = self.channels["video"]
            for idx, nal in enumerate(nals):
                last_nal = idx == len(nals) - 1
                for payload, last_frag in _fragment(nal, h265):
                    out.append(
                        self._rtp(ch, PT_VIDEO, self.v_seq, ts, self.v_ssrc, last_nal and last_frag, payload)
                    )
                    self.v_seq += 1
                    self.v_count += 1
                    self.v_octets += len(payload)
            out.extend(self._sender_reports())
        elif pkt.codec == CODEC_PCMA and "audio" in self.channels and self.got_keyframe:
            ch = self.channels["audio"]
            for i in range(0, len(pkt.payload), 960):
                chunk = pkt.payload[i : i + 960]
                out.append(self._rtp(ch, PT_PCMA, self.a_seq, self.a_ts, self.a_ssrc, True, chunk))
                self.a_seq += 1
                self.a_ts += len(chunk)
                self.a_count += 1
                self.a_octets += len(chunk)
        return out


def _fragment(nal: bytes, h265: bool):
    """Yield (rtp_payload, is_last) for one NAL (RFC 7798 / RFC 6184 FU)."""
    if len(nal) <= MAX_RTP_PAYLOAD:
        yield nal, True
        return
    if h265:
        nal_type = (nal[0] >> 1) & 0x3F
        head = bytes([(nal[0] & 0x81) | (49 << 1), nal[1]])
        data = nal[2:]
        fu = lambda s, e: bytes([(0x80 if s else 0) | (0x40 if e else 0) | nal_type])  # noqa: E731
    else:
        nal_type = nal[0] & 0x1F
        head = bytes([(nal[0] & 0xE0) | 28])
        data = nal[1:]
        fu = lambda s, e: bytes([(0x80 if s else 0) | (0x40 if e else 0) | nal_type])  # noqa: E731
    step = MAX_RTP_PAYLOAD - len(head) - 1
    for i in range(0, len(data), step):
        start, end = i == 0, i + step >= len(data)
        yield head + fu(start, end) + data[i : i + step], end


class RtspServer:
    """Serves rtsp://127.0.0.1:<port>/live for one CameraStreamer."""

    def __init__(self, streamer: CameraStreamer) -> None:
        self.streamer = streamer
        self.port: int | None = None
        self.path = "/" + base64.urlsafe_b64encode(os.urandom(9)).decode()
        self._server: asyncio.base_events.Server | None = None

    @property
    def url(self) -> str:
        return f"rtsp://127.0.0.1:{self.port}{self.path}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _sdp(self) -> str:
        p = self.streamer.params
        b64 = lambda k: base64.b64encode(p[k]).decode()  # noqa: E731
        lines = [
            "v=0",
            "o=- 0 0 IN IP4 127.0.0.1",
            "s=Xiaomi hlc7",
            "c=IN IP4 0.0.0.0",
            "t=0 0",
        ]
        if self.streamer.video_codec == CODEC_H265:
            lines += [
                f"m=video 0 RTP/AVP {PT_VIDEO}",
                f"a=rtpmap:{PT_VIDEO} H265/90000",
                f"a=fmtp:{PT_VIDEO} sprop-vps={b64('vps')}; sprop-sps={b64('sps')}; sprop-pps={b64('pps')}",
            ]
        else:
            sps = p["sps"]
            lines += [
                f"m=video 0 RTP/AVP {PT_VIDEO}",
                f"a=rtpmap:{PT_VIDEO} H264/90000",
                f"a=fmtp:{PT_VIDEO} packetization-mode=1; profile-level-id={sps[1:4].hex()}; "
                f"sprop-parameter-sets={b64('sps')},{b64('pps')}",
            ]
        lines += ["a=control:trackID=0"]
        lines += [
            f"m=audio 0 RTP/AVP {PT_PCMA}",
            f"a=rtpmap:{PT_PCMA} PCMA/8000",
            "a=control:trackID=1",
        ]
        return "\r\n".join(lines) + "\r\n"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        session = _Session(writer, loop)
        sender: asyncio.Task | None = None
        try:
            while True:
                first = await reader.read(1)
                if not first:
                    break
                if first == b"$":  # interleaved RTCP from the client: skip
                    hdr = await reader.readexactly(3)
                    await reader.readexactly(struct.unpack(">H", hdr[1:])[0])
                    continue
                head = first + await reader.readuntil(b"\r\n\r\n")
                lines = head.decode(errors="replace").split("\r\n")
                method, url, _ = (lines[0].split(" ") + ["", ""])[:3]
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                if int(headers.get("content-length", "0") or 0):
                    await reader.readexactly(int(headers["content-length"]))
                cseq = headers.get("cseq", "0")
                resp_headers: dict[str, str] = {}
                body = ""
                status = "200 OK"

                if method != "OPTIONS" and self.path not in url:
                    status = "404 Not Found"
                elif method == "OPTIONS":
                    resp_headers["Public"] = "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER"
                elif method == "DESCRIBE":
                    ok = await loop.run_in_executor(None, self.streamer.wait_ready, READY_TIMEOUT_S)
                    if not ok:
                        status = "503 Service Unavailable"
                        _LOGGER.warning("Camera stream not ready: %s", self.streamer.last_error)
                    else:
                        body = self._sdp()
                        resp_headers["Content-Type"] = "application/sdp"
                        resp_headers["Content-Base"] = url.rstrip("/") + "/"
                elif method == "SETUP":
                    transport = headers.get("transport", "")
                    if "interleaved" not in transport and "TCP" not in transport.upper():
                        status = "461 Unsupported Transport"
                    else:
                        kind = "audio" if "trackID=1" in url else "video"
                        ch = 2 if kind == "audio" else 0
                        session.channels[kind] = ch
                        resp_headers["Transport"] = f"RTP/AVP/TCP;unicast;interleaved={ch}-{ch + 1}"
                        resp_headers["Session"] = "1;timeout=60"
                elif method == "PLAY":
                    resp_headers["Session"] = "1"
                    if session.consumer_id is None:
                        session.consumer_id = self.streamer.add_consumer(session.on_packet)
                        sender = asyncio.create_task(self._send_loop(session))
                elif method == "GET_PARAMETER":
                    resp_headers["Session"] = "1"
                elif method == "TEARDOWN":
                    self._write(writer, cseq, status, resp_headers, body)
                    break
                else:
                    status = "405 Method Not Allowed"
                self._write(writer, cseq, status, resp_headers, body)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            if session.consumer_id is not None:
                self.streamer.remove_consumer(session.consumer_id)
            if sender:
                sender.cancel()
            writer.close()

    @staticmethod
    def _write(writer, cseq: str, status: str, headers: dict[str, str], body: str) -> None:
        out = [f"RTSP/1.0 {status}", f"CSeq: {cseq}", "Server: xiaomi_hlc7"]
        out += [f"{k}: {v}" for k, v in headers.items()]
        data = body.encode()
        if data:
            out.append(f"Content-Length: {len(data)}")
        writer.write(("\r\n".join(out) + "\r\n\r\n").encode() + data)

    @staticmethod
    async def _send_loop(session: _Session) -> None:
        try:
            while True:
                frame = await session.queue.get()
                if frame is None:
                    return
                session.writer.write(frame)
                if session.queue.empty():
                    await session.writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            return
