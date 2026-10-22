#!/usr/bin/env python3
"""macOS side of the phone's USB Ethernet live console (kernel sweetusb=2).

The kernel (patches 0075-0077) enumerates as a CDC-ECM device and streams
the console to UDP 192.168.7.1:5140 every 100 ms. This script waits for the
interface, gives it 192.168.7.1/24 and dumps the stream to a file.

    tools/sweet-usbnet-mac.py LOGFILE [--iface enX] [--wait 120]

Needs sudo for the address (or set the address by hand first).
"""
import argparse
import os
import socket
import subprocess
import sys
import time

PHONE_MAC = "02:00:5e:77:ee:01"
HOST_IP = "192.168.7.1"
PHONE_IP = "192.168.7.2"
UDP_PORT = 5140


def find_iface():
    out = subprocess.run(["ifconfig", "-l"], capture_output=True, text=True).stdout
    for name in out.split():
        try:
            info = subprocess.run(["ifconfig", name], capture_output=True, text=True).stdout
        except OSError:
            continue
        if PHONE_MAC in info:
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--iface")
    ap.add_argument("--wait", type=int, default=180)
    ap.add_argument("--no-addr", action="store_true")
    args = ap.parse_args()

    deadline = time.time() + args.wait
    iface = args.iface
    if not iface:
        print(f"waiting for the phone's USB Ethernet ({PHONE_MAC})...", file=sys.stderr)
        while time.time() < deadline:
            iface = find_iface()
            if iface:
                break
            time.sleep(1)
    if not iface:
        print("phone USB Ethernet never appeared", file=sys.stderr)
        return 1

    print(f"phone USB Ethernet: {iface}", file=sys.stderr)
    if not args.no_addr:
        pw = os.environ.get("SUDOPASS")
        if pw:
            r = subprocess.run(["sudo", "-S", "ifconfig", iface, "inet", HOST_IP,
                                "netmask", "255.255.255.0"],
                               input=pw + "\n", capture_output=True, text=True)
            if r.returncode != 0:
                print(f"ifconfig failed: {r.stderr.strip()}", file=sys.stderr)
        else:
            r = subprocess.run(["sudo", "-n", "ifconfig", iface, "inet", HOST_IP,
                                "netmask", "255.255.255.0"], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"run this by hand (sudo needs a password):\n"
                      f"  sudo ifconfig {iface} inet {HOST_IP} netmask 255.255.255.0",
                      file=sys.stderr)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", UDP_PORT))
    print(f"listening on udp/{UDP_PORT} -> {args.logfile}", file=sys.stderr)

    with open(args.logfile, "ab", buffering=0) as fh:
        while True:
            data, _ = sock.recvfrom(65535)
            fh.write(data)
            sys.stdout.write(data.decode("utf-8", "replace"))
            sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
