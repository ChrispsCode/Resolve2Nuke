"""
Resolve2Nuke.qt
===============

PySide2/6 shim. Nuke 17 ships PySide6 only, but everything imports Qt from
here so the code doesn't care.
"""

from __future__ import annotations

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2 as shiboken
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    import shiboken6 as shiboken


def is_valid(obj) -> bool:
    return obj is not None and shiboken.isValid(obj)
