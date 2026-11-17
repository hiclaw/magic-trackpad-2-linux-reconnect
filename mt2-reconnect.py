#!/usr/bin/env python3
"""Make an Apple Magic Trackpad 2 reconnect over Bluetooth after a reboot.

Run with the trackpad plugged in by Lightning cable. The script writes the
local adapter address into the trackpad's USB HID host report, seeds the BlueZ
device record with a link key, restarts BlueZ, then waits for the trackpad to
connect. Nothing is sent over the network and the key is never printed.

Tested on Arch Linux, kernel 7.2.15, BlueZ 5.87, firmware 0x0318.
"""

import argparse
import configparser
import fcntl
import glob
import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

USB_ID = "0003:000005AC:00000265"
REPORT_DEVICE, REPORT_HOST = 0x34, 0x35
STORAGE = Path("/var/lib/bluetooth")
ADDRESS = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
TEMPLATE = """\
[General]
Name=Magic Trackpad
Class=0x002594
SupportedTechnologies=BR/EDR;
Trusted=true
Blocked=false
Services=00001124-0000-1000-8000-00805f9b34fb;00001200-0000-1000-8000-00805f9b34fb;
"""


def fail(message):
    sys.exit(f"mt2-reconnect: {message}")


def hid_feature(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


def usb_node():
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = Path(node, "device/uevent").read_text().split()
        if f"HID_ID={USB_ID}" in uevent:
            return "/dev/" + os.path.basename(node)
    return None


def get_feature(fd, report):
    buf = bytearray(256)
    buf[0] = report
    length = fcntl.ioctl(fd, hid_feature(0x07, len(buf)), buf, True)
    return bytes(buf[:length])


def set_feature(fd, data):
    fcntl.ioctl(fd, hid_feature(0x06, len(data)), bytearray(data), True)


def mac(raw):
    return ":".join(f"{b:02X}" for b in raw)


def adapter():
    out = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True).stdout
    match = re.search(r"Controller ([0-9A-F:]{17})", out)
    if not match:
        fail("no powered Bluetooth controller found")
    return match.group(1)


def property(device, name):
    out = subprocess.run(
        ["bluetoothctl", "info", device], capture_output=True, text=True
    ).stdout
    match = re.search(rf"^\s*{name}: (.*)$", out, re.MULTILINE)
    return match.group(1).strip() if match else None


def write_record(path, key):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(path.read_text() if path.exists() else TEMPLATE)
    if not parser.has_section("General"):
        parser["General"] = {"Name": "Magic Trackpad", "Trusted": "true"}
    parser.remove_section("LinkKey")
    parser["LinkKey"] = {"Key": key.hex().upper(), "Type": "4", "PINLength": "0"}
    out = io.StringIO()
    parser.write(out, space_around_delimiters=False)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(out.getvalue())
    path.chmod(0o600)


def wait_for_unplug():
    print("Unplug the cable, switch the trackpad off, wait 3 seconds, switch it on.")
    while usb_node():
        time.sleep(1)


def wait_for_connection(device, timeout):
    deadline = time.monotonic() + timeout
    attempted = 0.0
    while time.monotonic() < deadline:
        if property(device, "Connected") == "yes":
            return True
        if time.monotonic() - attempted > 10:
            subprocess.run(
                ["bluetoothctl", "connect", device],
                capture_output=True,
                timeout=30,
            )
            attempted = time.monotonic()
        time.sleep(2)
    return property(device, "Connected") == "yes"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="run even if connected")
    parser.add_argument("--timeout", type=int, default=150, help="seconds to wait")
    args = parser.parse_args()

    if os.geteuid() != 0:
        fail("run as root; it writes /var/lib/bluetooth")

    node = usb_node()
    if not node:
        fail("plug the trackpad in with a Lightning cable first")

    fd = os.open(node, os.O_RDWR)
    try:
        device = mac(get_feature(fd, REPORT_DEVICE)[4:10])
        host_report = get_feature(fd, REPORT_HOST)
    finally:
        os.close(fd)
    if not ADDRESS.match(device):
        fail(f"unexpected trackpad address {device!r}")

    if property(device, "Connected") == "yes" and not args.force:
        print(f"{device} is connected; nothing to do (use --force to redo)")
        return 0

    host = adapter()
    print(f"adapter:  {host}")
    print(f"trackpad: {device}")

    key = os.urandom(16)
    report = bytearray(host_report)
    report[3:9] = bytes.fromhex(host.replace(":", ""))
    report[9:25] = key[::-1]
    fd = os.open(node, os.O_RDWR)
    try:
        set_feature(fd, bytes(report))
        written = mac(get_feature(fd, REPORT_HOST)[3:9])
    finally:
        os.close(fd)
    print(f"host address write: {'ok' if written == host else 'FAILED'}")

    subprocess.run(["systemctl", "stop", "bluetooth"], check=True)
    try:
        path = STORAGE / host / device / "info"
        write_record(path, key)
        print(f"device record: {path}")
    finally:
        subprocess.run(["systemctl", "start", "bluetooth"], check=True)
    time.sleep(2)

    wait_for_unplug()
    print("waiting for the trackpad to connect...")
    if not wait_for_connection(device, args.timeout):
        fail("no connection; switch the trackpad off and on again and retry")

    print(f"connected: {device}")
    for name in ("Paired", "Bonded", "Trusted", "Connected"):
        print(f"  {name}: {property(device, name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
