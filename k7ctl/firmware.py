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

DOWNLOAD_URL = "https://forum.fiio.com/firmwareDown.do"

UPDATE_HELP = f"""\
To update the K7's firmware, use FiiO's Windows updater. Without a Windows PC
you can run it in a Windows virtual machine (for example virt-manager/QEMU or
VirtualBox) and pass the K7 through to it as a USB device (2972:0047).
FiiO's firmware downloads: {DOWNLOAD_URL}
Compare the version there with the installed one shown above.
Keep the K7 plugged in and powered for the whole update."""

GENERIC_HELP = """\
Firmware updates for this DAC come from its manufacturer, usually as a Windows
or macOS tool. Without one, run it in a virtual machine with the DAC passed
through as a USB device. The version shown is the USB device version
(bcdDevice), which most manufacturers use as the firmware version."""


def update_help(k7) -> str:
    return UPDATE_HELP if (k7 is None or k7.is_k7) else GENERIC_HELP


def open_download_page() -> bool:
    """Open FiiO's firmware page in the browser (FiiO has no update API to check)."""
    import shutil
    import subprocess
    opener = shutil.which("xdg-open")
    if not opener:
        print(DOWNLOAD_URL)
        return False
    try:
        subprocess.Popen([opener, DOWNLOAD_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True


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
