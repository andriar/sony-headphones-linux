"""Click a sonyeq tray menu item by label over D-Bus (for testing). Usage: tray_click.py '<label>' | --dump"""
import sys

from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
watcher = bus.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                        "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", ("org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")),
                        None, 0, -1, None).unpack()[0]
item = next(i for i in watcher if i.endswith("/sonyeq_tray"))
dest, path = item.split("@", 1)
menu = path + "/Menu"
layout = bus.call_sync(dest, menu, "com.canonical.dbusmenu", "GetLayout",
                       GLib.Variant("(iias)", (0, -1, ["label"])), None, 0, -1, None).unpack()[1]


def find(node, label):
    nid, props, children = node
    if props.get("label") == label:
        return nid
    for child in children:
        if (hit := find(child, label)) is not None:
            return hit
    return None


if sys.argv[1] == "--dump":
    def walk(node, depth=0):
        nid, props, children = node
        if props.get("label"):
            print("  " * depth + props["label"])
        for c in children:
            walk(c, depth + 1)
    layout2 = bus.call_sync(dest, menu, "com.canonical.dbusmenu", "GetLayout",
                            GLib.Variant("(iias)", (0, 1, ["label", "enabled", "visible"])), None, 0, -1, None).unpack()[1]
    for nid, props, _ in layout2[2]:
        if props.get("label") and props.get("visible", True):
            print(("   " if props.get("enabled", True) else " x ") + props["label"])
    sys.exit(0)
nid = find(layout, sys.argv[1])
if nid is None:
    sys.exit(f"no menu item '{sys.argv[1]}'")
bus.call_sync(dest, menu, "com.canonical.dbusmenu", "Event",
              GLib.Variant("(isvu)", (nid, "clicked", GLib.Variant("i", 0), 0)), None, 0, -1, None)
print(f"clicked {sys.argv[1]} (id {nid})")
