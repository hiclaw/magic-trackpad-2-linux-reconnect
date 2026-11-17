#!/usr/bin/python3 -I
"""Make an Apple Magic Trackpad 2 reconnect over Bluetooth after a reboot.

Run with the trackpad plugged in by Lightning cable. The script writes the
local adapter address into the trackpad's USB HID host report, overwriting the
stored one, then seeds the BlueZ device record with a fresh link key and
waits for the trackpad to page the adapter and connect.

Never run this while the trackpad is paired to and connected with a Mac; it
follows the Mac until it is forgotten there.

Report layout follows philpax/magic-trackpad-2-linux-userspace (MIT).
Tested on Arch Linux, kernel 7.2.15, BlueZ 5.87, MT7922, firmware 0x0318.

Usage:
  trackpad-fix --status     show adapter, trackpad and stored host
  trackpad-fix              repair the pairing (needs root to write BlueZ state)
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

USB_ID = "0003:000005AC:00000265"
REPORT_DEVICE, REPORT_HOST = 0x34, 0x35
STORAGE = "/var/lib/bluetooth"
ADDRESS = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
TEMPLATE = """\
[General]
Name=Magic Trackpad
Class=0x002594
SupportedTechnologies=BR/EDR;
Trusted=true
Blocked=false
Services=00001124-0000-1000-8000-00805f9b34fb;00001200-0000-1000-8000-00805f9b34fb;
"""
PROPERTIES = ("Paired", "Bonded", "Trusted", "Connected")


def fail(message):
    sys.exit(f"trackpad-fix: {message}")


def hid_feature(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


def get_feature(fd, report):
    buf = bytearray(256)
    buf[0] = report
    length = fcntl.ioctl(fd, hid_feature(0x07, len(buf)), buf, True)
    return bytes(buf[:length])


def set_feature(fd, data):
    fcntl.ioctl(fd, hid_feature(0x06, len(data)), bytearray(data), True)


def mac(raw):
    return ":".join(f"{b:02X}" for b in raw)


def usb_node():
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(sysfs, "device/uevent")).read().split()
        except OSError:
            continue
        if f"HID_ID={USB_ID}" in uevent:
            return "/dev/" + os.path.basename(sysfs)
    return None


def with_node(node, func):
    fd = os.open(node, os.O_RDWR)
    try:
        return func(fd)
    finally:
        os.close(fd)


def read_reports(node):
    def read(fd):
        device = get_feature(fd, REPORT_DEVICE)
        host = get_feature(fd, REPORT_HOST)
        return mac(device[4:10]), host
    return with_node(node, read)


def adapter():
    out = subprocess.run(
        ["bluetoothctl", "show"], capture_output=True, text=True
    ).stdout
    match = re.search(r"Controller ([0-9A-F:]{17})", out)
    if not match:
        fail("no powered Bluetooth controller; turn Bluetooth on")
    return match.group(1)


def property(device, name):
    out = subprocess.run(
        ["bluetoothctl", "info", device], capture_output=True, text=True, timeout=30
    ).stdout
    match = re.search(rf"^\s*{name}: (.*)$", out, re.MULTILINE)
    return match.group(1).strip() if match else None


def write_record(path, key):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    if os.path.exists(path):
        directory = os.path.dirname(path)
        backup = os.path.join(
            os.path.dirname(directory),
            f"{os.path.basename(directory)}.info.{time.strftime('%Y%m%d-%H%M%S')}",
        )
        try:
            with open(path) as source, open(backup, "w") as target:
                target.write(source.read())
            os.chmod(backup, 0o600)
            print(f"backup:   {backup}")
            parser.read(path)
        except OSError:
            parser.read_string(TEMPLATE)
    else:
        parser.read_string(TEMPLATE)
    parser.remove_section("LinkKey")
    parser["LinkKey"] = {"Key": key.hex().upper(), "Type": "4", "PINLength": "0"}
    out = io.StringIO()
    parser.write(out, space_around_delimiters=False)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w") as target:
        target.write(out.getvalue())
    os.chmod(path, 0o600)


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
            try:
                subprocess.run(
                    ["bluetoothctl", "connect", device],
                    capture_output=True,
                    timeout=20,
                )
            except subprocess.TimeoutExpired:
                pass
            attempted = time.monotonic()
        time.sleep(2)
    return property(device, "Connected") == "yes"


def report(host, device, stored):
    print(f"adapter:  {host}")
    print(f"trackpad: {device}")
    print(f"stored host address: {stored}")
    for name in PROPERTIES:
        print(f"  {name}: {property(device, name)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true", help="report, change nothing")
    parser.add_argument("--force", action="store_true", help="redo a connected trackpad")
    parser.add_argument("--yes", action="store_true", help="do not confirm")
    parser.add_argument("--timeout", type=int, default=150, help="seconds to wait")
    args = parser.parse_args()

    node = usb_node()
    if not node:
        fail("no Magic Trackpad 2 on USB; plug in the Lightning cable")

    try:
        device, host_report = read_reports(node)
    except PermissionError:
        fail(f"cannot read {node}; rerun with sudo")

    host = adapter()

    if args.status:
        report(host, device, mac(host_report[3:9]))
        return 0

    if os.geteuid() != 0:
        os.execvp(
            "sudo",
            ["sudo", sys.executable, "-I", os.path.abspath(__file__), *sys.argv[1:]],
        )

    if not ADDRESS.match(device):
        fail(f"unexpected trackpad address {device!r}")
    if property(device, "Connected") == "yes" and not args.force:
        print(f"{device} is connected; nothing to do (--force to redo)")
        return 0
    if not os.path.isdir(os.path.join(STORAGE, host)):
        fail(f"{STORAGE}/{host} missing; turn Bluetooth on once and retry")

    report(host, device, mac(host_report[3:9]))
    if not args.yes and sys.stdin.isatty():
        answer = input("Write the adapter address and a fresh link key? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            return 0

    key = os.urandom(16)
    payload = bytearray(host_report)
    payload[3:9] = bytes.fromhex(host.replace(":", ""))
    payload[9:25] = key[::-1]
    with_node(node, lambda fd: set_feature(fd, bytes(payload)))
    written = mac(read_reports(node)[1][3:9])
    print(f"host address write: {'ok' if written == host else 'FAILED'}")
    if written != host:
        fail("the trackpad did not accept the address; is the cable a data cable?")

    subprocess.run(["systemctl", "stop", "bluetooth"], check=True)
    try:
        path = os.path.join(STORAGE, host, device, "info")
        write_record(path, key)
        print(f"device record: {path}")
    finally:
        subprocess.run(["systemctl", "start", "bluetooth"], check=True)
    time.sleep(2)

    if usb_node():
        wait_for_unplug()
    print("waiting for the trackpad to connect...")
    if not wait_for_connection(device, args.timeout):
        fail("no connection; switch the trackpad off and on again and retry")

    print(f"connected: {device}")
    for name in PROPERTIES:
        print(f"  {name}: {property(device, name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
