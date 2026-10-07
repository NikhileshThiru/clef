#!/usr/bin/env python3
"""Root helper: read Clevo fan duty through /dev/tuxedo_io and publish it for clefd.

Only the read ioctls are used. Output: /run/clef/fans.json (world readable), every 2 s.
The Clevo interface reports duty (0-255) and the EC's temperature per fan, not RPM.
Decoding follows tuxedo-control-center's tuxedo_io_api.hh.
"""
import fcntl
import json
import os
import struct
import time

IOCTL_MAGIC = 0xEC
MAGIC_READ_CL = IOCTL_MAGIC + 1
POINTER_SIZE = 8  # _IOR(..., int32_t*) encodes sizeof(pointer)


def _ior(kind: int, nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (kind << 8) | nr


R_CL_FANINFO = [_ior(MAGIC_READ_CL, 0x10 + i, POINTER_SIZE) for i in range(3)]
OUT = "/run/clef/fans.json"


def read_fans(fd: int) -> list[dict]:
    fans = []
    for i, req in enumerate(R_CL_FANINFO):
        buf = bytearray(8)
        try:
            fcntl.ioctl(fd, req, buf, True)
        except OSError:
            continue
        info = struct.unpack_from("<i", buf)[0]
        duty = info & 0xFF
        temp = struct.unpack("b", bytes([(info >> 16) & 0xFF]))[0]
        if temp <= 1:  # no fan in this slot
            continue
        fans.append({"fan": i, "percent": round(duty / 255 * 100), "temp": temp})
    return fans


def main() -> None:
    fd = os.open("/dev/tuxedo_io", os.O_RDONLY)
    while True:
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": time.time(), "fans": read_fans(fd)}, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
        time.sleep(2)


if __name__ == "__main__":
    main()
