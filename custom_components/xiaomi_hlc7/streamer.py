"""On-demand P2P session manager for one camera.

Opens the MISS/CS2 session when the first consumer (RTSP client, speaker)
needs it, fans media packets out to consumers, and closes the session a
while after the last consumer left. All blocking I/O runs in one worker
thread; consumers are plain callables invoked from that thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .xiaomi_cloud import XiaomiCloud
from .xiaomi_p2p import (
    CODEC_H264,
    CODEC_H265,
    MediaPacket,
    MissClient,
    generate_keypair,
)

_LOGGER = logging.getLogger(__name__)

IDLE_CLOSE_S = 30
RETRY_DELAYS_S = (2, 5, 10, 30, 60)
FRAME_BYTES = 320  # 40 ms of 8 kHz A-law
FRAME_S = 0.040

Consumer = Callable[[MediaPacket], None]


def split_nals(annexb: bytes) -> list[bytes]:
    """Split an Annex-B buffer into NAL units (start codes removed)."""
    nals: list[bytes] = []
    i = 0
    n = len(annexb)
    start = -1
    while i + 3 <= n:
        if annexb[i] == 0 and annexb[i + 1] == 0 and (
            annexb[i + 2] == 1 or (i + 4 <= n and annexb[i + 2] == 0 and annexb[i + 3] == 1)
        ):
            sc = 3 if annexb[i + 2] == 1 else 4
            if start >= 0:
                nals.append(annexb[start:i])
            start = i + sc
            i += sc
            continue
        i += 1
    if start >= 0 and start < n:
        nals.append(annexb[start:])
    return [nal.rstrip(b"\x00") or nal for nal in nals if nal]


class CameraStreamer:
    """Owns the P2P session of one camera."""

    def __init__(
        self,
        cloud: XiaomiCloud,
        cloud_lock: threading.Lock,
        region: str,
        did: str,
        host: str,
        quality: int = 2,
    ) -> None:
        self._cloud = cloud
        self._cloud_lock = cloud_lock
        self.region = region
        self.did = did
        self.host = host
        self.quality = quality

        self._lock = threading.Lock()
        self._consumers: dict[int, Consumer] = {}
        self._next_id = 1
        self._holds = 0  # speaker etc. keep the session open without consuming
        self._idle_since: float | None = None
        self._client: MissClient | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._speaker_lock = threading.Lock()

        self.video_codec: int | None = None
        self.params: dict[str, bytes] = {}  # vps/sps/pps for SDP
        self.last_error: str | None = None
        self.protocol: str | None = None
        self.remote: str | None = None

    # ----------------------------------------------------------- consumers
    def add_consumer(self, consumer: Consumer) -> int:
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._consumers[cid] = consumer
            self._idle_since = None
        self._ensure_thread()
        return cid

    def remove_consumer(self, cid: int) -> None:
        with self._lock:
            self._consumers.pop(cid, None)
            if not self._consumers and not self._holds:
                self._idle_since = time.monotonic()

    def wait_ready(self, timeout: float) -> bool:
        """Block until codec parameters are known (first keyframe).

        Parameters learned in an earlier session are reused, so a new RTSP
        client gets its SDP at once instead of waiting for the P2P session
        (cloud signature + handshake + keyframe), which can exceed the
        5-10 s read timeout of some clients (go2rtc in Frigate).
        """
        self._ensure_thread()
        if self._params_complete():
            return True
        return self._ready.wait(timeout)

    def _params_complete(self) -> bool:
        if self.video_codec == CODEC_H265:
            return all(k in self.params for k in ("vps", "sps", "pps"))
        if self.video_codec == CODEC_H264:
            return all(k in self.params for k in ("sps", "pps"))
        return False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def shutdown(self) -> None:
        self._stop.set()
        client = self._client
        if client:
            client.close()

    # -------------------------------------------------------------- worker
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name=f"xiaomi_hlc7-{self.did}"
            )
            self._thread.start()

    def _should_close(self) -> bool:
        with self._lock:
            if self._consumers or self._holds:
                return False
            if self._idle_since is None:
                self._idle_since = time.monotonic()
            return time.monotonic() - self._idle_since > IDLE_CLOSE_S

    def _connect(self) -> MissClient:
        pub, priv = generate_keypair()
        with self._cloud_lock:
            vendor = self._cloud.miss_vendor(self.region, self.did, pub)
        vid = (vendor.get("vendor") or {}).get("vendor")
        if vid != 4:
            raise RuntimeError(f"unsupported P2P vendor {vid} (only CS2 is implemented)")
        client = MissClient(self.host, pub, priv, vendor["public_key"], vendor["sign"])
        client.start_media(quality=self.quality, audio=True)
        return client

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if self._should_close():
                break
            try:
                self._client = self._connect()
                self.protocol = self._client.protocol
                self.remote = self._client.remote
                self.last_error = None
                attempt = 0
                _LOGGER.debug("P2P session open (%s %s)", self.protocol, self.remote)
                self._pump(self._client)
            except Exception as err:  # noqa: BLE001
                self.last_error = str(err)
                _LOGGER.warning("Camera %s P2P session failed: %s", self.host, err)
            finally:
                if self._client:
                    try:
                        self._client.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._client = None
            if self._stop.is_set() or self._should_close():
                break
            delay = RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)]
            attempt += 1
            self._stop.wait(delay)
        self._ready.clear()
        _LOGGER.debug("P2P session closed")

    def _pump(self, client: MissClient) -> None:
        while not self._stop.is_set():
            if self._should_close():
                return
            pkt = client.read_packet(timeout=10)
            if pkt.codec in (CODEC_H264, CODEC_H265):
                self._learn_params(pkt)
            with self._lock:
                consumers = list(self._consumers.values())
            for consumer in consumers:
                try:
                    consumer(pkt)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("consumer failed")

    def _learn_params(self, pkt: MediaPacket) -> None:
        self.video_codec = pkt.codec
        for nal in split_nals(pkt.payload):
            if pkt.codec == CODEC_H265:
                kind = {32: "vps", 33: "sps", 34: "pps"}.get((nal[0] >> 1) & 0x3F)
            else:
                kind = {7: "sps", 8: "pps"}.get(nal[0] & 0x1F)
            if kind:
                self.params[kind] = nal
        needed = ("vps", "sps", "pps") if pkt.codec == CODEC_H265 else ("sps", "pps")
        if all(k in self.params for k in needed):
            self._ready.set()

    # ------------------------------------------------------------- speaker
    def play_alaw(self, alaw: bytes, timeout: float = 20) -> None:
        """Play 8 kHz A-law audio on the camera speaker (blocking)."""
        with self._lock:
            self._holds += 1
            self._idle_since = None
        try:
            self._ensure_thread()
            deadline = time.monotonic() + timeout
            while self._client is None:
                if time.monotonic() > deadline:
                    raise RuntimeError(self.last_error or "camera did not connect")
                time.sleep(0.1)
            client = self._client
            with self._speaker_lock:
                client.start_speaker()
                time.sleep(1.0)  # camera needs a moment before it plays
                t0 = time.monotonic()
                for n, i in enumerate(range(0, len(alaw), FRAME_BYTES)):
                    client.write_audio(alaw[i : i + FRAME_BYTES])
                    delay = t0 + (n + 1) * FRAME_S - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                time.sleep(0.3)
                client.stop_speaker()
        finally:
            with self._lock:
                self._holds -= 1
                if not self._consumers and not self._holds:
                    self._idle_since = time.monotonic()
