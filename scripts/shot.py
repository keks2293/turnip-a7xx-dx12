#!/usr/bin/env python3
"""Screenshot via xdg-desktop-portal (org.freedesktop.portal.Screenshot).

Needed because KWin ScreenShot2 rejects direct calls:
"Error.NoAuthorized: The process is not authorized to take a screenshot".
The portal — the only authorized path on Wayland, and it returns
a ready file by URI, so nothing has to be decoded.

Run: shot.py out.png [timeout-seconds]
"""
import shutil
import sys
import urllib.parse

import dbus
import dbus.mainloop.glib
from gi.repository import GLib


def main(out_path, timeout_s=45):
    loop = GLib.MainLoop()
    result = {}

    gloop = dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(mainloop=gloop)

    # Subscribe BEFORE the call: otherwise the Response may arrive before we
    # connect. connect_signal exists only on ProxyObject, Interface has it
    # not — its call would go to the bus as a normal method and fail with TypeError.
    def on_response(response, results, path=None):
        if path is not None and str(path) != str(result.get("path")):
            return
        result["response"] = int(response)
        result["results"] = results
        loop.quit()

    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface="org.freedesktop.portal.Request",
        bus_name="org.freedesktop.portal.Desktop",
        path_keyword="path",
    )

    desktop = bus.get_object("org.freedesktop.portal.Desktop",
                             "/org/freedesktop/portal/desktop")
    shot = dbus.Interface(desktop, "org.freedesktop.portal.Screenshot")

    token = "rp6shot" + str(abs(hash(out_path)) % 10**6)
    opts = {
        "interactive": dbus.Boolean(False),
        "handle_token": dbus.String(token),
    }
    result["path"] = shot.Screenshot("", opts)

    GLib.timeout_add(timeout_s * 1000, lambda: (loop.quit(), False)[1])
    loop.run()

    if "results" not in result:
        print("portal did not reply within %d s (probably waiting for consent in the dialog)"
              % timeout_s, file=sys.stderr)
        return 2
    if result["response"] != 0:
        print("portal rejected: response=%d" % result["response"], file=sys.stderr)
        return 1

    uri = str(result["results"].get("uri", ""))
    if not uri:
        print("no uri in the reply: %r" % (dict(result["results"]),), file=sys.stderr)
        return 1
    path = urllib.parse.unquote(uri[len("file://"):])
    shutil.copyfile(path, out_path)
    print("%s <- %s" % (out_path, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/shots/shot.png",
                  int(sys.argv[2]) if len(sys.argv) > 2 else 45))
