"""Read the Creator 5's state over the home network. Read-only: this module asks the printer one
question, `/detail`, and can send it nothing else.

The protocol is as documented by the community at Parallel-7/flashforge-api-docs (Creator 5 Series,
Discovery Protocol, endpoints_creator5_1.9.2.yaml): the printer answers a UDP probe with a 276-byte
packet, and serves HTTP on port 8898. On stock firmware HTTP answers only while the printer is in
LAN-only mode, which turns the Flashforge cloud off; in cloud mode it replies code -2.

The check code the printer asks for is kept in the macOS Keychain, never in a file here.
"""

from __future__ import annotations

import json
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request

PROBES = (("225.0.0.9", 19000), ("255.255.255.255", 48899))     # multicast and broadcast; the printer listens on both
MODELS = {40: "Creator 5", 41: "Creator 5 Pro"}                 # product ids in the discovery packet
ENDPOINT = "/detail"                                            # the only request this module makes
KEYCHAIN_SERVICE = "creator5-slicer-mcp-printer"                # account = the printer's serial number
LAN_ONLY = ("The printer answers status requests only in LAN-only mode, which is set on its network screen. "
            "LAN-only mode turns the Flashforge cloud off: the phone app and cloud printing stop until it is turned back.")


def _text(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("utf-8", "ignore").strip()


def parse(packet: bytes, address: str) -> dict | None:
    """One discovery reply, or None when it is not a modern Flashforge packet."""
    if len(packet) < 196:
        return None
    pid = int.from_bytes(packet[0x88:0x8A], "big")
    return {"name": _text(packet[0:128]), "address": address, "serial_number": _text(packet[0x92:0x92 + 128]),
            "model": MODELS.get(pid, f"product id {pid}"), "creator5": pid in MODELS,
            "http_port": int.from_bytes(packet[0x8E:0x90], "big") or 8898,
            "mode": "LAN-only" if packet[0x90] == 0 else "cloud", "cloud_connected": packet[0x91] == 1}


def discover(wait: float = 2.0, probes: tuple = PROBES) -> list[dict]:
    """Every Creator 5 that answers a probe on this network within `wait` seconds. Needs no credential."""
    found: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in probes:
            try:
                sock.sendto(b"\0", target)          # the printer ignores what is sent and answers any packet
            except OSError:
                continue                            # no route for one kind of probe: the other may still reach it
        until = time.time() + wait
        while time.time() < until:
            sock.settimeout(max(until - time.time(), 0.05))
            try:
                packet, sender = sock.recvfrom(1024)
            except (socket.timeout, OSError):
                break
            reply = parse(packet, sender[0])
            if reply and reply["creator5"]:
                found[reply["serial_number"]] = reply
    return list(found.values())


def check_code(serial_number: str) -> str | None:
    """The printer's check code from the macOS Keychain, or None. It is read when asked for and never kept."""
    if platform.system() != "Darwin":
        return None
    try:
        done = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", serial_number, "-w"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def _ask(printer: dict, code: str, wait: float) -> dict:
    body = json.dumps({"serialNumber": printer["serial_number"], "checkCode": code}).encode()
    request = urllib.request.Request(f"http://{printer['address']}:{printer['http_port']}{ENDPOINT}", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=wait) as reply:
        return json.loads(reply.read().decode("utf-8", "ignore"))


def status(wait: float = 2.0, probes: tuple = PROBES, code_for=check_code) -> dict:
    """What the printer is doing now. Never raises: each way it can fail is an answer saying what to do."""
    printers = discover(wait, probes)
    if not printers:
        return {"found": False, "message": f"No Creator 5 answered on this network within {wait:g} seconds. It may be off, "
                "on another network, or macOS may not yet have allowed this app to find devices on the local network "
                "(System Settings, Privacy and Security, Local Network)."}
    printer = printers[0]
    out = {"found": True, **{k: printer[k] for k in ("name", "model", "address", "serial_number", "mode", "cloud_connected")},
           "others_found": len(printers) - 1, "status_read": False}
    code = code_for(printer["serial_number"])
    if not code:
        out["message"] = ("The printer was found, but its check code is not stored on this Mac, so its status cannot be read. "
                          "The code is shown on the printer's network screen. Store it once by running this in Terminal, "
                          "which asks for the code and keeps it in the Keychain: "
                          f"security add-generic-password -s {KEYCHAIN_SERVICE} -a {printer['serial_number']} -w   " + LAN_ONLY)
        return out
    try:
        reply = _ask(printer, code, max(wait, 5.0))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["message"] = f"The printer was found but did not answer a status request: {exc}"
        return out
    if reply.get("code") == -2:
        out["message"] = "The printer is in cloud mode. " + LAN_ONLY
        return out
    detail = reply.get("detail")
    if reply.get("code") != 0 or not isinstance(detail, dict):
        said = str(reply.get("message") or reply.get("code"))
        fix = (" The stored check code is not the printer's. Read it from the printer's network screen and store it again, "
               f"after: security delete-generic-password -s {KEYCHAIN_SERVICE} -a {printer['serial_number']}"
               if "code" in said.lower() else "")
        out["message"] = f"The printer refused the status request: {said}.{fix}"
        return out
    station = detail.get("matlStationInfo") or {}
    out.update({
        "status_read": True,
        "state": detail.get("status"),
        "error_code": detail.get("errorCode") or None,
        "firmware": detail.get("firmwareVersion"),
        "file": detail.get("printFileName") or None,
        "progress": detail.get("printProgress"),
        "layer": detail.get("printLayer"), "layers": detail.get("targetPrintLayer"),
        "seconds_elapsed": detail.get("printDuration"), "seconds_left": detail.get("estimatedTime"),
        "nozzle_c": detail.get("nozzleTemps"), "nozzle_target_c": detail.get("nozzleTargetTemps"),
        "bed_c": detail.get("platTemp"), "bed_target_c": detail.get("platTargetTemp"),
        "door": detail.get("doorStatus"),
        "toolhead_in_use": station.get("currentSlot") or None,
        "loaded": [{"toolhead": slot.get("slotId"), "has_filament": slot.get("hasFilament"),
                    "material": slot.get("materialName"), "colour": slot.get("materialColor")}
                   for slot in station.get("slotInfos") or []],
    })
    return out
