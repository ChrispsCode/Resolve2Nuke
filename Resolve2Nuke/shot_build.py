"""
Resolve2Nuke.shot_build
=======================

Turns a ShotInfo into root settings and nodes in the current Nuke script.

Every root change is a toggle in ShotOptions (all on by default), so a
secondary plate can be pulled in without touching the script's settings. The
GUI will show ShotOptions as checkboxes.
"""

from __future__ import annotations

from dataclasses import dataclass

import nuke

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke.resolve_bridge import ShotInfo


@dataclass
class ShotOptions:
    set_ocio: bool = True
    set_fps: bool = True
    fps_source: str = "timeline"   # "timeline" (1:1 frames back in Resolve) or "clip"
    set_format: bool = True
    set_frame_range: bool = True
    handles: int = 0
    create_write: bool = True


@dataclass
class BuildResult:
    read: nuke.Node
    write: nuke.Node | None
    unknown_colorspace: bool


# #----------------------------------------------------------------------------
# # Root settings

def _set_ocio() -> None:
    root = nuke.root()
    root["colorManagement"].setValue("OCIO")
    root["OCIO_config"].setValue(config.OCIO_CONFIG)
    if root["OCIO_config"].value() != config.OCIO_CONFIG:
        logger.warning(f"Couldn't set the OCIO config to {config.OCIO_CONFIG}")


def _shot_fps(shot: ShotInfo, options: ShotOptions) -> float:
    return shot.clip_fps if options.fps_source == "clip" else shot.timeline_fps


def _find_or_add_format(width: int, height: int) -> nuke.Format:
    """Reuse a square-pixel format of this size, else add one."""
    for fmt in nuke.formats():
        if fmt.width() == width and fmt.height() == height and fmt.pixelAspect() == 1.0:
            return fmt
    name = f"R2N_{width}x{height}"
    return nuke.addFormat(f"{width} {height} 1.0 {name}")


def _set_root(shot: ShotInfo, options: ShotOptions) -> None:
    root = nuke.root()
    if options.set_ocio:
        _set_ocio()
    if options.set_fps:
        root["fps"].setValue(_shot_fps(shot, options))
    if options.set_format:
        root["format"].setValue(_find_or_add_format(shot.width, shot.height))
    if options.set_frame_range:
        # # Set last first, so first never ends up after last on the way.
        root["last_frame"].setValue(shot.source_last + options.handles)
        root["first_frame"].setValue(shot.source_first - options.handles)
        root["lock_range"].setValue(True)


# #----------------------------------------------------------------------------
# # Nodes

def _add_resolve_knobs(node: nuke.Node, shot: ShotInfo) -> None:
    """Remember where the plate came from, for the send-back step."""
    p = config.KNOB_PREFIX
    node.addKnob(nuke.Tab_Knob(config.KNOB_TAB, "Resolve2Nuke"))
    for name, label, value in [
        ("project", "project", shot.project_name),
        ("timeline", "timeline", shot.timeline_name),
        ("item_id", "item id", shot.item_id),
        ("record_in_tc", "record in TC", shot.record_in_tc),
        ("resolve_colorspace", "Resolve colour space", shot.resolve_colorspace),
    ]:
        knob = nuke.String_Knob(p + name, label)
        knob.setValue(value)
        knob.setEnabled(False)
        node.addKnob(knob)
    for name, label, value in [
        ("track", "video track", shot.track_index),
        ("record_in", "record in", shot.record_in),
        ("record_out", "record out", shot.record_out),
        ("source_first", "source first", shot.source_first),
        ("source_last", "source last", shot.source_last),
    ]:
        knob = nuke.Int_Knob(p + name, label)
        knob.setValue(value)
        knob.setEnabled(False)
        node.addKnob(knob)
    knob = nuke.Double_Knob(p + "timeline_fps", "timeline fps")
    knob.setValue(shot.timeline_fps)
    knob.setEnabled(False)
    node.addKnob(knob)


def _create_read(shot: ShotInfo) -> tuple[nuke.Node, bool]:
    """Returns the Read, and whether its colour space was unknown."""
    for n in nuke.selectedNodes():
        n.setSelected(False)
    read = nuke.createNode("Read", inpanel=False)

    # # fromUserText scans the file's frame range. nuke.nodes.Read(file=...) leaves it at 1-1.
    if shot.is_sequence:
        read["file"].fromUserText(f"{shot.file_path} {shot.source_first}-{shot.source_last}")
    else:
        read["file"].fromUserText(shot.file_path)

    if read["file_type"].value() == "braw":
        read["decode_using"].setValue(config.BRAW_DECODE_USING)

    ocio_space = config.RESOLVE_TO_OCIO.get(shot.resolve_colorspace)
    unknown = ocio_space is None
    if not unknown:
        read["colorspace"].setValue(ocio_space)

    read["label"].setValue(f"{shot.timeline_name} V{shot.track_index}\n{shot.source_first}-{shot.source_last}")
    _add_resolve_knobs(read, shot)
    return read, unknown


def _create_write(read: nuke.Node) -> nuke.Node:
    for n in nuke.selectedNodes():
        n.setSelected(False)
    read.setSelected(True)
    write = nuke.createNode("Write", inpanel=False)  # # connects to and sits under the selected Read
    write["file_type"].setValue("exr")
    write["file"].setValue(config.WRITE_FILE)
    write["colorspace"].setValue(config.WRITE_COLORSPACE)
    write["datatype"].setValue(config.WRITE_DATATYPE)
    write["create_directories"].setValue(True)
    return write


# #----------------------------------------------------------------------------
# # Entry point

def build(shot: ShotInfo, options: ShotOptions | None = None) -> BuildResult:
    """Apply the shot to the current script in one undo step."""
    options = options or ShotOptions()
    read = write = None
    unknown = False

    nuke.Undo.begin("Pull shot from Resolve")
    try:
        # # Root first: the Read and Write colorspace menus list the root config's spaces.
        _set_root(shot, options)

        # # Nuke sets the project range from the first Read in an empty script,
        # # so put it back when the range isn't ours to change.
        root = nuke.root()
        range_before = (root["first_frame"].value(), root["last_frame"].value())
        read, unknown = _create_read(shot)
        if not options.set_frame_range:
            root["first_frame"].setValue(range_before[0])
            root["last_frame"].setValue(range_before[1])
        if options.create_write:
            write = _create_write(read)
    finally:
        nuke.Undo.end()

    if options.set_fps and shot.clip_fps and abs(shot.clip_fps - shot.timeline_fps) > 0.001:
        logger.warning(f"Clip is {shot.clip_fps:g} fps but the timeline is {shot.timeline_fps:g} fps. "
                       f"Resolve retimes the clip, so source frames won't map 1:1 to timeline frames.")
    return BuildResult(read=read, write=write, unknown_colorspace=unknown)
