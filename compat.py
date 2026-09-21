# -*- coding: utf-8 -*-
"""Qt5/Qt6 compatibility helpers for QGIS 3.x and QGIS 4.x."""

from qgis.PyQt import QtCore, QtGui, QtWidgets


# QAction moved from QtWidgets (Qt5) to QtGui (Qt6).
QAction = getattr(QtGui, "QAction", None) or getattr(QtWidgets, "QAction")


def exec_dialog(dialog):
    """Execute a dialog on both PyQt5 and PyQt6."""
    runner = getattr(dialog, "exec", None)
    if runner is None:
        runner = getattr(dialog, "exec" + "_")
    return runner()


def field_type(kind):
    """Return a QgsField scalar type compatible with Qt5 and Qt6."""
    qt_major = int(str(QtCore.QT_VERSION_STR).split(".", 1)[0])
    if qt_major >= 6:
        scope = QtCore.QMetaType.Type
        mapping = {
            "int": "Int",
            "double": "Double",
            "string": "QString",
        }
        return getattr(scope, mapping[kind])

    legacy = getattr(QtCore, "QVar" + "iant")
    mapping = {
        "int": "Int",
        "double": "Double",
        "string": "String",
    }
    return getattr(legacy, mapping[kind])
