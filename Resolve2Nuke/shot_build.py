"""
Resolve2Nuke.shot_build
=======================

Turns a ShotInfo into root settings and nodes in the current Nuke script.

Every root change is a toggle in ShotOptions (all on by default), so a
secondary plate can be pulled in without touching the script's settings. The
GUI will show ShotOptions as checkboxes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import nuke

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke import stamp
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
    # # A reference branch with the clip's Resolve grade as a LUT (grade_lut.py)
    include_grade: bool = False
    # # Template plate: Resolve renders the clip with its effects (template_plate.py).
    render_template: bool = False
    template_format: str = config.DEFAULT_TEMPLATE_FORMAT
    template_clip_res: bool = True


@dataclass
class BuildResult:
    read: nuke.Node
    write: nuke.Node | None
    unknown_colorspace: bool
    kronos: nuke.Node | None = None
    template: nuke.Node | None = None
    grade: nuke.Node | None = None


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

    if shot.retimed:
        lo, hi = int(min(shot.retime_frames)), int(max(shot.retime_frames))
        read["label"].setValue(f"{shot.timeline_name} V{shot.track_index}\nsource {lo}-{hi}, retimed")
    else:
        read["label"].setValue(f"{shot.timeline_name} V{shot.track_index}\n{shot.source_first}-{shot.source_last}")
    stamp.write_stamp(read, stamp.Stamp.from_shot(shot))
    return read, unknown


def retime_keys(shot: ShotInfo) -> list[tuple[int, float]]:
    """(output frame, source frame) for every output frame. Whole frames when
    Resolve uses Nearest (it floors), fractional ones for frame blend / optical flow."""
    keys = []
    for i, source in enumerate(shot.retime_frames):
        value = float(math.floor(source + 1e-6)) if shot.retime_nearest else source
        keys.append((shot.source_first + i, value))
    return keys


def _create_kronos(read: nuke.Node, shot: ShotInfo) -> nuke.Node:
    """Resolve's retime as a Kronos in Frame mode, with a key on every output frame."""
    for n in nuke.selectedNodes():
        n.setSelected(False)
    read.setSelected(True)
    kronos = nuke.createNode("Kronos", inpanel=False)  # # connects to and sits under the Read
    kronos["timing2"].setValue("Frame")
    knob = kronos["timingFrame2"]
    knob.setAnimated()
    for frame, value in retime_keys(shot):
        knob.setValueAt(value, frame)
    curve = knob.animation(0)
    curve.changeInterpolation(curve.keys(), nuke.LINEAR)
    kind = "ramp" if shot.is_ramp else f"{shot.speed:g}%"
    mode = "nearest" if shot.retime_nearest else "blended"
    kronos["label"].setValue(f"Resolve retime: {kind}\n{mode}")
    return kronos


def _create_write(read: nuke.Node, shot: ShotInfo) -> nuke.Node:
    for n in nuke.selectedNodes():
        n.setSelected(False)
    read.setSelected(True)
    write = nuke.createNode("Write", inpanel=False)  # # connects to and sits under the selected node
    write["file_type"].setValue("exr")
    write["file"].setValue(config.WRITE_FILE)
    write["colorspace"].setValue(config.WRITE_COLORSPACE)
    write["datatype"].setValue(config.WRITE_DATATYPE)
    write["create_directories"].setValue(True)
    # # The Write carries its own stamp, so send-back doesn't depend on the B pipe reaching the Read.
    stamp.write_stamp(write, stamp.Stamp.from_shot(shot))
    return write


# #----------------------------------------------------------------------------
# # Entry point

def build(shot: ShotInfo, options: ShotOptions | None = None) -> BuildResult:
    """Apply the shot to the current script in one undo step."""
    options = options or ShotOptions()
    read = write = kronos = None
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
        if shot.retimed:
            kronos = _create_kronos(read, shot)
        if options.create_write:
            write = _create_write(kronos or read, shot)
    finally:
        nuke.Undo.end()

    return BuildResult(read=read, write=write, unknown_colorspace=unknown, kronos=kronos)
