#!/usr/bin/env python3
"""Standalone test of the go2rtc-free Xiaomi stack (run in the HA Terminal).

    apk add python3 py3-cryptography        # once, HA Terminal add-on
    python3 /config/custom_components/xiaomi_hlc7/hlc7_test.py

What it does (nothing is changed on the camera):
1. Logs in to your Mi account (region cn by default). Captcha / SMS / e-mail
   code are asked interactively. The session (userId + passToken) is saved
   to /config/.xiaomi_hlc7_session.json (chmod 600) so re-runs skip login.
2. Lists your cameras and shows the miIO token of the chosen one.
3. Asks the cloud to sign a P2P key (miss_get_vendor).
4. Connects straight to the camera over LAN (CS2 UDP), authenticates and
   records SECONDS of video/audio to /config/www/hlc7_test.h265 / .alaw
5. Reconnects with the SAME signature to check whether it can be reused
   (fewer cloud calls).
6. With --beep: plays a 2 s beep on the camera speaker.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xiaomi_cloud import XiaomiCloud, XiaomiCloudError, XiaomiLoginStep  # noqa: E402
from xiaomi_p2p import (  # noqa: E402
    CODEC_H264,
    CODEC_H265,
    CODEC_PCMA,
    MissClient,
    P2PError,
    generate_keypair,
    pcm16_to_alaw,
)

_BASE = "/config" if os.path.isdir("/config") else os.path.expanduser("~")
SESSION_FILE = os.path.join(_BASE, ".xiaomi_hlc7_session.json")
OUT_DIR = os.path.join(_BASE, "www") if _BASE == "/config" else _BASE


def login(cloud: XiaomiCloud) -> None:
    if os.path.exists(SESSION_FILE):
        try:
            saved = json.load(open(SESSION_FILE))
            cloud.login_with_token(saved["user_id"], saved["pass_token"])
            print(f"[ok] Sessão salva reutilizada (userId {cloud.user_id})")
            return
        except (XiaomiCloudError, KeyError, ValueError) as err:
            print(f"[..] Sessão salva não serviu ({err}); login com senha")

    user = input("Conta Mi (e-mail, telefone ou ID): ").strip()
    password = getpass.getpass("Senha: ")
    step = lambda: cloud.login(user, password)  # noqa: E731
    while True:
        try:
            step()
            break
        except XiaomiLoginStep as need:
            if need.captcha:
                os.makedirs(OUT_DIR, exist_ok=True)
                path = os.path.join(OUT_DIR, "hlc7_captcha.jpg")
                with open(path, "wb") as f:
                    f.write(need.captcha)
                print("[..] Captcha salvo. Abra http://<HA>:8123/local/hlc7_captcha.jpg")
                code = input("Texto do captcha: ").strip()
                step = lambda c=code: cloud.login_captcha(c)  # noqa: E731
            else:
                dest = need.verify_phone or need.verify_email or "seu telefone/e-mail"
                code = input(f"Código enviado para {dest}: ").strip()
                step = lambda c=code: cloud.login_verify(c)  # noqa: E731

    fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"user_id": cloud.user_id, "pass_token": cloud.pass_token}, f)
    print(f"[ok] Login feito (userId {cloud.user_id}); sessão salva em {SESSION_FILE}")


def record(miss: MissClient, seconds: float, prefix: str | None) -> dict[str, int]:
    stats = {"video": 0, "keyframes": 0, "audio": 0, "bytes": 0, "codec": 0}
    vf = open(prefix + ".h265", "wb") if prefix else None
    af = open(prefix + ".alaw", "wb") if prefix else None
    first = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pkt = miss.read_packet()
        if first is None:
            first = time.monotonic()
        stats["bytes"] += len(pkt.payload)
        if pkt.codec in (CODEC_H264, CODEC_H265):
            stats["video"] += 1
            stats["codec"] = pkt.codec
            # H.265 VPS (type 32) / H.264 SPS (type 7) start a keyframe group
            nal = pkt.payload[4] if pkt.payload[:4] == b"\x00\x00\x00\x01" else pkt.payload[3]
            if (pkt.codec == CODEC_H265 and (nal >> 1) & 0x3F == 32) or (
                pkt.codec == CODEC_H264 and nal & 0x1F == 7
            ):
                stats["keyframes"] += 1
            if vf:
                vf.write(pkt.payload)
        elif pkt.codec == CODEC_PCMA:
            stats["audio"] += 1
            if af:
                af.write(pkt.payload)
    if vf:
        vf.close()
        af.close()
    stats["first_packet_ms"] = int(((first or time.monotonic()) - (end - seconds)) * 1000)
    return stats


def beep(miss: MissClient) -> None:
    miss.start_speaker()
    time.sleep(1)
    pcm = struct.pack(
        f"<{16000}h",
        *(int(12000 * math.sin(2 * math.pi * 880 * i / 8000)) for i in range(16000)),
    )
    alaw = pcm16_to_alaw(pcm)
    for i in range(0, len(alaw), 320):  # 40 ms frames
        miss.write_audio(alaw[i : i + 320])
        time.sleep(0.04)
    miss.stop_speaker()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="cn")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--model", default="isa.camera.hlc7")
    ap.add_argument("--did", help="did da câmera (se tiver mais de uma)")
    ap.add_argument("--beep", action="store_true")
    args = ap.parse_args()

    cloud = XiaomiCloud()
    login(cloud)

    cams = [d for d in cloud.devices(args.region) if ".camera." in d.get("model", "")]
    for d in cams:
        print(f"     - {d.get('name')}  model={d.get('model')}  did={d.get('did')}  ip={d.get('localip')}")
    cam = next(
        (
            d
            for d in cams
            if (str(d.get("did")) == args.did if args.did else d.get("model") == args.model)
        ),
        None,
    )
    if not cam:
        sys.exit(f"[erro] Nenhuma câmera {args.model} na região {args.region}")
    token = cam.get("token", "")
    print(f"[ok] Câmera: {cam['name']} did={cam['did']} ip={cam['localip']}")
    print(f"[ok] Token miIO: {token[:4]}...{token[-4:]}  (o componente busca sozinho)")

    pub, priv = generate_keypair()
    t0 = time.monotonic()
    vendor = cloud.miss_vendor(args.region, cam["did"], pub)
    vid = (vendor.get("vendor") or {}).get("vendor")
    print(f"[ok] miss_get_vendor em {int((time.monotonic()-t0)*1000)} ms; vendor={vid} (4=cs2)")
    if vid != 4:
        sys.exit(f"[erro] vendor {vid} não suportado neste teste: {json.dumps(vendor)[:300]}")

    os.makedirs(OUT_DIR, exist_ok=True)
    prefix = os.path.join(OUT_DIR, "hlc7_test")
    t0 = time.monotonic()
    miss = MissClient(cam["localip"], pub, priv, vendor["public_key"], vendor["sign"])
    print(f"[ok] CS2 + auth em {int((time.monotonic()-t0)*1000)} ms, remoto {miss.remote}")
    try:
        miss.start_media(quality=2, audio=True)
        stats = record(miss, args.seconds, prefix)
        print(f"[ok] Mídia: {stats}")
        print(f"     Arquivos: {prefix}.h265 e {prefix}.alaw")
        if args.beep:
            beep(miss)
            print("[ok] Bip enviado ao alto-falante (você ouviu?)")
    finally:
        miss.close()

    time.sleep(2)
    try:
        miss2 = MissClient(cam["localip"], pub, priv, vendor["public_key"], vendor["sign"])
        miss2.start_media(quality=2, audio=False)
        s2 = record(miss2, 3, None)
        miss2.close()
        print(f"[ok] Reuso da assinatura FUNCIONA (sem nova chamada à nuvem): {s2['video']} pacotes de vídeo")
    except P2PError as err:
        print(f"[..] Reuso da assinatura falhou: {err}")


if __name__ == "__main__":
    try:
        main()
    except (XiaomiCloudError, P2PError) as err:
        sys.exit(f"[erro] {err}")
