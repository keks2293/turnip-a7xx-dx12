#!/usr/bin/env python3
"""Снимок экрана через xdg-desktop-portal (org.freedesktop.portal.Screenshot).

Нужен потому, что KWin ScreenShot2 отклоняет прямые вызовы:
"Error.NoAuthorized: The process is not authorized to take a screenshot".
Портал — единственный авторизованный путь на Wayland, и он возвращает
готовый файл по URI, так что ничего декодировать не приходится.

Запуск: shot.py out.png [таймаут-секунд]
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

    # Подписываемся ДО вызова: иначе Response может прийти раньше, чем
    # подключимся. connect_signal есть только у ProxyObject, у Interface его
    # нет — его вызов ушёл бы в шину как обычный метод и падал с TypeError.
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
        print("портал не ответил за %d с (вероятно, ждёт согласия в диалоге)"
              % timeout_s, file=sys.stderr)
        return 2
    if result["response"] != 0:
        print("портал отклонил: response=%d" % result["response"], file=sys.stderr)
        return 1

    uri = str(result["results"].get("uri", ""))
    if not uri:
        print("в ответе нет uri: %r" % (dict(result["results"]),), file=sys.stderr)
        return 1
    path = urllib.parse.unquote(uri[len("file://"):])
    shutil.copyfile(path, out_path)
    print("%s <- %s" % (out_path, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/shots/shot.png",
                  int(sys.argv[2]) if len(sys.argv) > 2 else 45))
