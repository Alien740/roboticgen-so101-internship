"""Xbox-360-protocol gamepads read straight over USB, for pads the browser can't see.

On a Mac, a Logitech F710 in X mode (and other XInput pads) has no driver, so the browser's Gamepad API
never reports it. This thread reads it with libusb (pyusb + libusb-package) and keeps its state in the
browser's "standard" layout; the page falls back to it (GET /api/pad) when it sees no gamepad itself.
Optional: without pyusb the dashboard simply has no USB gamepad.
"""
from __future__ import annotations

import struct
import threading
import time

PADS = {(0x046D, 0xC21F): "Logitech F710 (X mode, via USB)",
        (0x045E, 0x028E): "Xbox 360 controller (via USB)",
        (0x046D, 0xC21D): "Logitech F310 (X mode, via USB)",
        (0x046D, 0xC21E): "Logitech F510 (X mode, via USB)"}
# report bytes 2-3 -> standard-mapping button index
BITS = {(2, 0x01): 12, (2, 0x02): 13, (2, 0x04): 14, (2, 0x08): 15, (2, 0x10): 9, (2, 0x20): 8,
        (2, 0x40): 10, (2, 0x80): 11, (3, 0x01): 4, (3, 0x02): 5, (3, 0x04): 16,
        (3, 0x10): 0, (3, 0x20): 1, (3, 0x40): 2, (3, 0x80): 3}


class XPad:
    def __init__(self):
        self.state = {"connected": False, "id": "", "axes": [0.0] * 4, "buttons": [[False, 0.0]] * 17, "t": 0.0}
        self.error = ""
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="xpad", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()

    def snapshot(self):
        s = dict(self.state)
        s["buttons"] = [{"pressed": p, "value": v} for p, v in s["buttons"]]
        return s

    def _loop(self):
        try:
            import libusb_package
            import usb.core
            import usb.util
            backend = libusb_package.get_libusb1_backend()
        except Exception as e:                       # optional dependency
            self.error = f"no USB gamepad support ({type(e).__name__})"
            return
        while not self._stop.is_set():
            dev = name = None
            for (vid, pid), nm in PADS.items():
                dev = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
                if dev is not None:
                    name = nm
                    break
            if dev is None:
                self.state = {**self.state, "connected": False}
                self._stop.wait(2.0)
                continue
            try:
                try:
                    dev.set_configuration()
                except usb.core.USBError:
                    pass                             # already configured
                intf = dev.get_active_configuration()[(0, 0)]
                usb.util.claim_interface(dev, intf.bInterfaceNumber)
                ep_in = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
                ep_out = next((e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT), None)
                if ep_out is not None:
                    try:
                        dev.write(ep_out.bEndpointAddress, bytes([0x01, 0x03, 0x06]), timeout=200)   # LED: player 1
                    except usb.core.USBError:
                        pass
                self.state = {**self.state, "connected": True, "id": name}
                self.error = ""
                while not self._stop.is_set():
                    try:
                        r = bytes(dev.read(ep_in.bEndpointAddress, 32, timeout=200))
                    except usb.core.USBTimeoutError:
                        continue                     # reports come only on change
                    if len(r) >= 14 and r[0] == 0x00 and r[1] >= 0x14:
                        self._parse(r)
            except Exception as e:                   # unplugged / asleep: look for it again
                self.error = f"{type(e).__name__}: {e}"
                self.state = {**self.state, "connected": False}
                try:
                    usb.util.dispose_resources(dev)
                except Exception:
                    pass
                self._stop.wait(1.0)

    def _parse(self, r):
        lx, ly, rx, ry = struct.unpack_from("<hhhh", r, 6)
        axes = [lx / 32768.0, -ly / 32768.0, rx / 32768.0, -ry / 32768.0]        # browser: down = +
        buttons = [[False, 0.0] for _ in range(17)]
        for (byte, bit), i in BITS.items():
            if r[byte] & bit:
                buttons[i] = [True, 1.0]
        for i, byte in ((6, 4), (7, 5)):                                       # triggers 0-255
            v = r[byte] / 255.0
            buttons[i] = [v > 0.5, v]
        self.state = {"connected": True, "id": self.state["id"], "axes": axes, "buttons": buttons, "t": time.time()}
