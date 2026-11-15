"""Talk to a Creator 5 over the local network.

Uses the same local HTTP interface OrcaSlicer's Flashforge print host uses
(port 8898, `detail` and `uploadGcode`). The printer must be in LAN mode; the
serial number and check code are shown on its screen.

EXPERIMENTAL: written from OrcaSlicer's source, not yet confirmed on hardware.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import uuid
from pathlib import Path

from .config import Settings

PORT = 8898


class PrinterError(RuntimeError):
    pass


def _credentials(settings: Settings) -> tuple[str, str, str]:
    missing = [name for name, value in (
        ("C5_PRINTER_IP", settings.printer_ip),
        ("C5_PRINTER_SERIAL", settings.printer_serial),
        ("C5_PRINTER_CHECK_CODE", settings.printer_check_code)) if not value]
    if missing:
        raise PrinterError("Printer not configured (missing " + ", ".join(missing) + "). Save its details once with: "
                           "python -m creator5_mcp.cli --save-printer IP SERIAL CHECKCODE")
    return settings.printer_ip, settings.printer_serial, settings.printer_check_code  # type: ignore[return-value]


def _post(ip: str, path: str, body: bytes, headers: dict[str, str], timeout: int) -> dict:
    # http.client is used directly: it keeps header names in the exact case the
    # printer documents, and it never routes a LAN request through a proxy.
    connection = http.client.HTTPConnection(ip, PORT, timeout=timeout)
    try:
        connection.putrequest("POST", "/" + path)
        for key, value in {**headers, "Content-Length": str(len(body))}.items():
            connection.putheader(key, value)
        connection.endheaders()
        connection.send(body)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "ignore")
    except OSError as exc:
        raise PrinterError(f"Printer not reachable at {ip}:{PORT}: {exc}") from exc
    finally:
        connection.close()
    if response.status >= 400:
        raise PrinterError(f"Printer answered HTTP {response.status}: {text[:300]}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PrinterError(f"Printer sent an unreadable reply: {text[:300]}") from exc
    if isinstance(data, dict) and data.get("code", 0) not in (0, "0"):
        raise PrinterError(f"Printer refused the request: {data.get('message') or data}")
    return data


def status(settings: Settings, timeout: int = 10) -> dict:
    ip, serial, code = _credentials(settings)
    payload = json.dumps({"serialNumber": serial, "checkCode": code}).encode()
    data = _post(ip, "detail", payload, {"Content-Type": "application/json"}, timeout)
    return data.get("detail", data) if isinstance(data, dict) else {"raw": data}


def upload(settings: Settings, gcode: Path, start_print: bool = False, level_bed: bool = True,
           timeout: int = 600) -> dict:
    ip, serial, code = _credentials(settings)
    if not gcode.is_file():
        raise PrinterError(f"File not found: {gcode}")
    if gcode.suffix.lower() not in (".gcode", ".3mf", ".g"):
        raise PrinterError("Only sliced .gcode or .3mf files are accepted by the printer.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", gcode.name)
    content = gcode.read_bytes()
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"gcodeFile\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()

    def flag(value: bool) -> str:
        return "true" if value else "false"

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "serialNumber": serial,
        "checkCode": code,
        "fileSize": str(len(content)),
        "printNow": flag(start_print),
        "levelingBeforePrint": flag(level_bed),
        "flowCalibration": "false",
        "firstLayerInspection": "false",
        "timeLapseVideo": "false",
        "useMatlStation": "false",
        "gcodeToolCnt": "1",
        "materialMappings": base64.b64encode(b"[]").decode(),
    }
    reply = _post(ip, "uploadGcode", body, headers, timeout)
    return {"uploaded": name, "bytes": len(content), "print_started": start_print, "printer_reply": reply}
