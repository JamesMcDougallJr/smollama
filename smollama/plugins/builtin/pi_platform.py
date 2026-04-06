"""Raspberry Pi platform detection helpers."""


def is_pi5() -> bool:
    """Return True if running on a Raspberry Pi 5.

    Reads /proc/device-tree/model (e.g. 'Raspberry Pi 5 Model B Rev 1.0').
    Returns False on Pi 4, Pi 3, non-Pi Linux, macOS, or unreadable file.
    """
    try:
        with open("/proc/device-tree/model") as f:
            return "Raspberry Pi 5" in f.read()
    except OSError:
        return False
