"""
Resolve2Nuke.template_plate
===========================

The Nuke side of template plates: where they go in the shot, the next version,
moving Resolve's render out of the staging folder, and the plate's Read.

Only works when hozed, so the destination is always the right shot:
    $SHOT_ROOT/plates/<SHOT_NAME>_plate_v##/<SHOT_NAME>_plate_v##.%04d.exr
    $SHOT_ROOT/plates/<SHOT_NAME>_plate_v##.mov
Versions count across both formats.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass

import nuke

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke import stamp
from Resolve2Nuke.resolve_bridge import ShotInfo
from Resolve2Nuke.template_render import RenderedPlate


class TemplateError(RuntimeError):
    """A problem making the template plate, with a message meant for the artist."""


@dataclass
class PlateTarget:
    name: str            # <SHOT_NAME>_plate_v##
    version: int
    path: str            # the Read's file path (%04d for EXR)
    folder: str          # $SHOT_ROOT/plates
    is_movie: bool


def hoz_context() -> tuple[str, str] | None:
    """(SHOT_ROOT, SHOT_NAME) when hozed, else None. Read at call time: re-hozing changes them."""
    root, name = os.environ.get("SHOT_ROOT"), os.environ.get("SHOT_NAME")
    return (root, name) if root and name else None


def next_version(plates_dir: str, shot_name: str) -> int:
    """One more than the highest existing <shot>_plate_v## (folder or .mov)."""
    pattern = re.compile(rf"^{re.escape(shot_name)}_plate_v(\d+)(\.mov)?$")
    versions = [int(m.group(1)) for f in (os.listdir(plates_dir) if os.path.isdir(plates_dir) else [])
                if (m := pattern.match(f))]
    return max(versions, default=0) + 1


def target(fmt: str) -> PlateTarget:
    """Where the next template plate would go. Raises TemplateError when not hozed."""
    context = hoz_context()
    if context is None:
        raise TemplateError("Template plates need a hoz context (SHOT_ROOT and SHOT_NAME), "
                            "so they land in the right shot.")
    shot_root, shot_name = context
    folder = os.path.join(shot_root, config.TEMPLATE_DIR)
    version = next_version(folder, shot_name)
    name = config.TEMPLATE_NAME.format(shot=shot_name, version=version)
    is_movie = config.TEMPLATE_FORMATS[fmt][3]
    pattern = config.TEMPLATE_MOVIE if is_movie else config.TEMPLATE_SEQUENCE
    return PlateTarget(name=name, version=version, path=os.path.join(folder, pattern.format(name=name)),
                       folder=folder, is_movie=is_movie)


_FRAME_RE = re.compile(r"(\d+)\.exr$")


def move_into_shot(rendered: RenderedPlate, plate: PlateTarget) -> None:
    """Move Resolve's render to the plate path, renaming EXR frames to %04d."""
    if os.path.exists(plate.path if plate.is_movie else os.path.dirname(plate.path)):
        raise TemplateError(f"{plate.name} already exists. Not overwriting it.")
    os.makedirs(os.path.dirname(plate.path), exist_ok=True)
    if plate.is_movie:
        movies = [f for f in rendered.files if f.lower().endswith(".mov")]
        if len(movies) != 1:
            raise TemplateError(f"Expected one .mov from Resolve, got {len(rendered.files)} file(s) in {rendered.folder}.")
        shutil.move(movies[0], plate.path)
    else:
        for f in rendered.files:
            match = _FRAME_RE.search(os.path.basename(f))
            if not match:
                continue
            shutil.move(f, plate.path % int(match.group(1)))
    shutil.rmtree(rendered.folder, ignore_errors=True)


def create_read(plate: PlateTarget, shot: ShotInfo, beside: nuke.Node | None = None) -> nuke.Node:
    """The template plate's Read, stamped like the hero Read, beside it in the graph."""
    for n in nuke.selectedNodes():
        n.setSelected(False)
    read = nuke.createNode("Read", inpanel=False)
    if plate.is_movie:
        read["file"].fromUserText(plate.path)
        # # A movie reads as frames 1-N; start it at the comp's first frame.
        read["frame_mode"].setValue("start at")
        read["frame"].setValue(str(shot.source_first))
    else:
        read["file"].fromUserText(f"{plate.path} {shot.source_first}-{shot.source_last}")
    read["colorspace"].setValue(config.TEMPLATE_COLORSPACE)
    read["label"].setValue(f"Resolve template plate v{plate.version:02d}\n{shot.source_first}-{shot.source_last}")
    stamp.write_stamp(read, stamp.Stamp.from_shot(shot))
    if beside is not None:
        read.setXYpos(beside.xpos() + 150, beside.ypos())
    read.setSelected(False)
    logger.info(f"Template plate Read {read.name()}: {plate.path}")
    return read
