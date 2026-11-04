"""Firmware information (read only).

The K7 has a USB DFU (Device Firmware Update) interface, which FiiO's Windows
updater uses. k7ctl deliberately does NOT flash firmware: FiiO's update
format isn't documented, and a bad flash can leave the DAC unusable. This
module only reports the installed version and what the update interface
advertises, read from the USB descriptors the kernel already has in /sys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DFU_CLASS, DFU_SUBCLASS = 0xFE, 0x01
DFU_FUNCTIONAL = 0x21

UPDATE_HELP = """\
To update the K7's firmware, use FiiO's Windows updater. Without a Windows PC
you can run it in a Windows virtual machine (for example virt-manager/QEMU or
VirtualBox) and pass the K7 through to it as a USB device (2972:0047).
Downloads: https://www.fiio.com/k7 (Downloads / Firmware).
Keep the K7 plugged in and powered for the whole update."""


@dataclass
class DfuInfo:
    can_download: bool
    can_upload: bool
    manifestation_tolerant: bool
    will_detach: bool
    detach_timeout_ms: int
    transfer_size: int


def parse_dfu(descriptors: bytes) -> DfuInfo | None:
    """Find the DFU functional descriptor in a raw USB descriptor blob."""
    i, in_dfu = 0, False
    while i + 2 <= len(descriptors):
        length, dtype = descriptors[i], descriptors[i + 1]
        if length < 2:
            break
        d = descriptors[i:i + length]
        if dtype == 0x04 and length >= 9:            # interface
            in_dfu = d[5] == DFU_CLASS and d[6] == DFU_SUBCLASS
        elif dtype == DFU_FUNCTIONAL and in_dfu and length >= 7:
            attrs = d[2]
            return DfuInfo(
                can_download=bool(attrs & 0x01),
                can_upload=bool(attrs & 0x02),
                manifestation_tolerant=bool(attrs & 0x04),
                will_detach=bool(attrs & 0x08),
                detach_timeout_ms=d[3] | (d[4] << 8),
                transfer_size=d[5] | (d[6] << 8),
            )
        i += length
    return None


def read_dfu(usb_path: str | None) -> DfuInfo | None:
    if not usb_path:
        return None
    try:
        return parse_dfu((Path(usb_path) / "descriptors").read_bytes())
    except OSError:
        return None
