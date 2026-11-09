"""Xiaomi account login and signed Mi Home cloud API calls.

Ported to Python from go2rtc (pkg/xiaomi/cloud.go and internal/xiaomi),
Copyright (c) 2022 AlexxIT, MIT License.

Blocking (urllib) on purpose: no third-party dependencies, so the same file
runs inside Home Assistant (in an executor) and in a plain python3 shell.
"""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SID = "xiaomiio"
ACCOUNT = "https://account.xiaomi.com"
USER_AGENT = (
    "Android-7.1.1-1.0.0-ONEPLUS A3010-136-"
    + "".join(secrets.choice(string.ascii_uppercase) for _ in range(13))
    + " APP/xiaomi.smarthome APPV/62830"
)
TIMEOUT = 15


class XiaomiCloudError(Exception):
    """Generic cloud failure."""


class XiaomiLoginStep(XiaomiCloudError):
    """Login needs user input: a captcha image or a verification code."""

    def __init__(
        self,
        captcha: bytes | None = None,
        verify_phone: str | None = None,
        verify_email: str | None = None,
    ) -> None:
        super().__init__("login needs user input")
        self.captcha = captcha
        self.verify_phone = verify_phone
        self.verify_email = verify_email


def base_url(region: str) -> str:
    if region in ("de", "i2", "ru", "sg", "us"):
        return f"https://{region}.api.io.mi.com/app"
    return "https://api.io.mi.com/app"


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    # Xiaomi drops the first 1024 bytes of keystream.
    for n in range(1024 + len(data)):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        if n >= 1024:
            out.append(data[n - 1024] ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def _signature(method: str, path: str, data: str, rc4_hash: str | None, signed_nonce: bytes) -> str:
    s = f"{method}&{path}&data={data}"
    if rc4_hash is not None:
        s += f"&rc4_hash__={rc4_hash}"
    s += "&" + base64.b64encode(signed_nonce).decode()
    return base64.b64encode(hashlib.sha1(s.encode()).digest()).decode()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


def _parse_login(body: bytes) -> dict[str, Any]:
    prefix = b"&&&START&&&"
    if not body.startswith(prefix):
        raise XiaomiCloudError(f"unexpected login answer: {body[:200]!r}")
    return json.loads(body[len(prefix) :])


class XiaomiCloud:
    """Holds one logged-in Xiaomi account session."""

    def __init__(self) -> None:
        self.user_id: str | None = None
        self.pass_token: str | None = None
        self._ssecurity: bytes | None = None
        self._cookies = ""
        self._auth: dict[str, str] = {}
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar), _NoRedirect()
        )

    # ---------------------------------------------------------------- http
    def _http(
        self,
        url: str,
        form: dict[str, str] | None = None,
        cookies: str | None = None,
        method: str | None = None,
    ) -> tuple[int, dict[str, str], bytes, list[tuple[str, str]]]:
        data = urllib.parse.urlencode(form).encode() if form else None
        req = urllib.request.Request(
            url, data=data, method=method or ("POST" if form is not None else "GET")
        )
        req.add_header("User-Agent", USER_AGENT)
        if data:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if cookies:
            req.add_header("Cookie", cookies)
        try:
            resp = self._opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as err:  # 3xx land here with _NoRedirect
            resp = err
        except (urllib.error.URLError, OSError) as err:
            raise XiaomiCloudError(f"cannot reach {url.split('?')[0]}: {err}") from err
        with resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            set_cookies = []
            for raw in resp.headers.get_all("Set-Cookie") or []:
                name, _, rest = raw.partition("=")
                set_cookies.append((name.strip(), rest.split(";", 1)[0]))
            return resp.status, headers, body, set_cookies

    # --------------------------------------------------------------- login
    def login(self, username: str, password: str) -> None:
        """Start (or retry) a password login. May raise XiaomiLoginStep."""
        _s, _h, body, _c = self._http(f"{ACCOUNT}/pass/serviceLogin?_json=true&sid={SID}")
        v1 = _parse_login(body)

        form = {
            "_json": "true",
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),  # noqa: S324
            "sid": v1.get("sid", SID),
            "callback": v1.get("callback", ""),
            "_sign": v1.get("_sign", ""),
            "qs": v1.get("qs", ""),
            "user": username,
        }
        cookies = "deviceId=" + "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(16)
        )
        if self._auth.get("captcha_code"):
            form["captCode"] = self._auth["captcha_code"]
            cookies += "; ick=" + self._auth.get("ick", "")

        _s, _h, body, _c = self._http(
            f"{ACCOUNT}/pass/serviceLoginAuth2", form=form, cookies=cookies
        )
        v2 = _parse_login(body)
        self._auth = {"username": username, "password": password}

        if v2.get("captchaUrl") or v2.get("captchaURL"):
            self._get_captcha(v2.get("captchaUrl") or v2["captchaURL"])
        if v2.get("notificationUrl"):
            self._auth_start(v2["notificationUrl"])
        if not v2.get("location"):
            raise XiaomiCloudError(f"login failed: {v2.get('desc') or v2}")

        self._auth = {}
        self._ssecurity = base64.b64decode(v2["ssecurity"])
        self.pass_token = v2.get("passToken")
        self._finish_auth(v2["location"])

    def login_captcha(self, code: str) -> None:
        self._auth["captcha_code"] = code
        if self._auth.get("flag"):
            self._send_ticket()
            return
        self.login(self._auth["username"], self._auth["password"])

    def login_verify(self, ticket: str) -> None:
        url = (
            f"{ACCOUNT}/identity/auth/verify{self._verify_name()}"
            f"?_flag={self._auth['flag']}&ticket={urllib.parse.quote(ticket)}"
            "&trust=false&_json=true"
        )
        _s, _h, body, _c = self._http(
            url,
            cookies="identity_session=" + self._auth.get("identity_session", ""),
            method="POST",
        )
        v = _parse_login(body)
        if not v.get("location"):
            raise XiaomiCloudError(f"verification failed: {v}")
        self._auth = {}
        self._finish_auth(v["location"])

    def login_with_token(self, user_id: str, pass_token: str) -> None:
        """Silent re-login with the long-lived passToken (no password)."""
        _s, _h, body, _c = self._http(
            f"{ACCOUNT}/pass/serviceLogin?_json=true&sid={SID}",
            cookies=f"userId={user_id}; passToken={pass_token}",
        )
        v = _parse_login(body)
        if not v.get("location") or not v.get("ssecurity"):
            raise XiaomiCloudError("saved Xiaomi session expired, log in again")
        self._ssecurity = base64.b64decode(v["ssecurity"])
        self.pass_token = v.get("passToken") or pass_token
        self.user_id = user_id
        self._finish_auth(v["location"])

    def _get_captcha(self, captcha_url: str) -> None:
        _s, _h, body, cookies = self._http(ACCOUNT + captcha_url)
        for name, value in cookies:
            if name == "ick":
                self._auth["ick"] = value
        raise XiaomiLoginStep(captcha=body)

    def _auth_start(self, notification_url: str) -> None:
        url = notification_url.replace("/fe/service/identity/authStart", "/identity/list")
        _s, _h, body, cookies = self._http(url)
        v = _parse_login(body)
        self._auth["flag"] = str(v.get("flag", ""))
        for name, value in cookies:
            if name == "identity_session":
                self._auth["identity_session"] = value
        self._send_ticket()

    def _verify_name(self) -> str:
        return {"4": "Phone", "8": "Email"}.get(self._auth.get("flag", ""), "")

    def _send_ticket(self) -> None:
        name = self._verify_name()
        cookies = "identity_session=" + self._auth.get("identity_session", "")
        _s, _h, body, _c = self._http(
            f"{ACCOUNT}/identity/auth/verify{name}?_flag={self._auth['flag']}&_json=true",
            cookies=cookies,
        )
        v1 = _parse_login(body)
        capt = self._auth.get("captcha_code", "")
        if capt:
            cookies += "; ick=" + self._auth.get("ick", "")
        _s, _h, body, _c = self._http(
            f"{ACCOUNT}/identity/auth/send{name}Ticket",
            form={"_json": "true", "icode": capt, "retry": "0"},
            cookies=cookies,
        )
        v2 = _parse_login(body)
        if v2.get("captchaUrl") or v2.get("captchaURL"):
            self._get_captcha(v2.get("captchaUrl") or v2["captchaURL"])
        if v2.get("code") != 0:
            raise XiaomiCloudError(f"could not send code: {v2}")
        raise XiaomiLoginStep(
            verify_phone=v1.get("maskedPhone"), verify_email=v1.get("maskedEmail")
        )

    def _finish_auth(self, location: str) -> None:
        values: dict[str, str] = {}
        url: str | None = location
        for _ in range(10):
            if not url:
                break
            status, headers, _body, cookies = self._http(url)
            for name, value in cookies:
                values[name] = value
            pragma = headers.get("extension-pragma")
            if pragma:
                ssec = json.loads(pragma).get("ssecurity")
                if ssec:
                    self._ssecurity = base64.b64decode(ssec)
            url = headers.get("location") if 300 <= status < 400 else None
            if url and url.startswith("/"):
                parts = urllib.parse.urlsplit(location)
                url = f"{parts.scheme}://{parts.netloc}{url}"
        if values.get("userId"):
            self.user_id = values["userId"]
        if values.get("passToken"):
            self.pass_token = values["passToken"]
        if not values.get("serviceToken"):
            raise XiaomiCloudError("login finished without serviceToken")
        self._cookies = (
            f"userId={self.user_id}; cUserId={values.get('cUserId', '')}; "
            f"serviceToken={values['serviceToken']}"
        )

    # ------------------------------------------------------------- api
    def request(self, region: str, path: str, params: dict[str, Any] | str) -> Any:
        if self._ssecurity is None:
            raise XiaomiCloudError("not logged in")
        data = params if isinstance(params, str) else json.dumps(params, separators=(",", ":"))
        nonce = os.urandom(8) + int(time.time() // 60).to_bytes(4, "big")
        signed_nonce = hashlib.sha256(self._ssecurity + nonce).digest()

        rc4_hash = _signature("POST", path, data, None, signed_nonce)
        enc_data = base64.b64encode(_rc4(signed_nonce, data.encode())).decode()
        enc_hash = base64.b64encode(_rc4(signed_nonce, rc4_hash.encode())).decode()
        form = {
            "data": enc_data,
            "rc4_hash__": enc_hash,
            "signature": _signature("POST", path, enc_data, enc_hash, signed_nonce),
            "_nonce": base64.b64encode(nonce).decode(),
        }
        req = urllib.request.Request(
            base_url(region) + path,
            data=urllib.parse.urlencode(form).encode(),
            method="POST",
        )
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Cookie", self._cookies)
        req.add_header("x-xiaomi-protocal-flag-cli", "PROTOCAL-HTTP2")
        req.add_header("MIOT-ENCRYPT-ALGORITHM", "ENCRYPT-RC4")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                body = resp.read()
        except urllib.error.HTTPError as err:
            raise XiaomiCloudError(f"{path}: HTTP {err.code}") from err
        except (urllib.error.URLError, OSError) as err:
            raise XiaomiCloudError(f"{path}: {err}") from err
        try:
            plain = _rc4(signed_nonce, base64.b64decode(body))
            answer = json.loads(plain)
        except ValueError:
            answer = json.loads(body)
        if answer.get("code") != 0:
            raise XiaomiCloudError(f"{path}: {answer.get('message') or answer}")
        return answer.get("result")

    # ---------------------------------------------------------- helpers
    def devices(self, region: str) -> list[dict[str, Any]]:
        result = self.request(region, "/v2/home/device_list_page", {})
        return result.get("list") or []

    def miss_vendor(self, region: str, did: str, app_pubkey: bytes) -> dict[str, Any]:
        """Ask the cloud to sign our P2P public key for this camera."""
        return self.request(
            region,
            "/v2/device/miss_get_vendor",
            {
                "app_pubkey": app_pubkey.hex(),
                "did": did,
                "support_vendors": "TUTK_CS2_MTP",
            },
        )
