"""
Resolve2Nuke.settings
=====================

Per-user memory of the dialogs' last-used values (QSettings), switched by
config.REMEMBER_SETTINGS. Only the "how" values are remembered (fps source,
handles, template format and resolution, placement, bin). The checkboxes
always start at their defaults: root settings ticked, template plate off.

Bad or missing stored values fall back to the defaults.
"""

from __future__ import annotations

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke.qt import QtCore
from Resolve2Nuke.resolve_bridge import PLACEMENTS
from Resolve2Nuke.send_back import SendOptions
from Resolve2Nuke.shot_build import ShotOptions

FPS_SOURCES = ("timeline", "clip")


def _store() -> QtCore.QSettings:
    return QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP)


def load_shot_options() -> ShotOptions:
    options = ShotOptions()
    if not config.REMEMBER_SETTINGS:
        return options
    try:
        s = _store()
        fps_source = s.value("pull/fps_source", options.fps_source)
        if fps_source in FPS_SOURCES:
            options.fps_source = fps_source
        options.handles = max(0, int(s.value("pull/handles", options.handles)))
        fmt = s.value("pull/template_format", options.template_format)
        if fmt in config.TEMPLATE_FORMATS:
            options.template_format = fmt
        options.template_clip_res = str(s.value("pull/template_clip_res", options.template_clip_res)).lower() in ("true", "1")
    except Exception as e:
        logger.warning(f"Couldn't read remembered pull settings, using defaults ({e})")
    return options


def save_shot_options(options: ShotOptions) -> None:
    if not config.REMEMBER_SETTINGS:
        return
    s = _store()
    s.setValue("pull/fps_source", options.fps_source)
    s.setValue("pull/handles", int(options.handles))
    s.setValue("pull/template_format", options.template_format)
    s.setValue("pull/template_clip_res", bool(options.template_clip_res))


def load_send_options() -> SendOptions:
    options = SendOptions()
    if not config.REMEMBER_SETTINGS:
        return options
    try:
        s = _store()
        placement = s.value("send/placement", options.placement)
        if placement in PLACEMENTS:
            options.placement = placement
        bin_text = s.value("send/bin", options.bin)
        if isinstance(bin_text, str) and bin_text.strip():
            options.bin = bin_text
    except Exception as e:
        logger.warning(f"Couldn't read remembered send settings, using defaults ({e})")
    return options


def save_send_options(options: SendOptions) -> None:
    if not config.REMEMBER_SETTINGS:
        return
    s = _store()
    s.setValue("send/placement", options.placement)
    s.setValue("send/bin", options.bin)
