# -*- coding: utf-8 -*-
def classFactory(iface):
    from .plugin import GeoVisualLineamentPlugin
    return GeoVisualLineamentPlugin(iface)
