"""
Resolve2Nuke.grade_lut
======================

A reference view of the clip's Resolve grade: the grade exported as a .cube
LUT, applied by a "ResolveGrade" group on a branch off the plate.

It's for looking at, not for baking: the group isn't in the Write's pipe, and
the grade is copied from the original clip onto the comp in Resolve.

The LUT is the grade only, in DaVinci Intermediate (see config.GRADE_LUT_SPACE).
Vectorfield's own colorspace knobs only offer Nuke's built-in curves, so the
group converts with OCIO on either side: scene_linear -> DaVinci Intermediate
-> LUT -> back.
"""

from __future__ import annotations

import os
import re

import nuke

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke.template_plate import hoz_context


class GradeError(RuntimeError):
    """A problem bringing in the grade, with a message meant for the artist."""


def lut_path() -> str:
    """The next $SHOT_ROOT/plates/<SHOT_NAME>_grade_v##.cube. Raises GradeError when not hozed."""
    context = hoz_context()
    if context is None:
        raise GradeError("The grade LUT needs a hoz context (SHOT_ROOT and SHOT_NAME), so it lands in the right shot.")
    shot_root, shot_name = context
    folder = os.path.join(shot_root, config.GRADE_LUT_DIR)
    pattern = re.compile(rf"^{re.escape(shot_name)}_grade_v(\d+)\.cube$")
    existing = [int(m.group(1)) for f in (os.listdir(folder) if os.path.isdir(folder) else []) if (m := pattern.match(f))]
    version = max(existing, default=0) + 1
    return os.path.join(folder, config.GRADE_LUT_NAME.format(shot=shot_name, version=version))


def create_group(source: nuke.Node, path: str) -> nuke.Node:
    """The ResolveGrade group, fed from source, beside the main pipe."""
    for n in nuke.selectedNodes():
        n.setSelected(False)
    group = nuke.nodes.Group()
    group.setName(config.GRADE_GROUP_NAME)
    group.begin()
    try:
        inp = nuke.nodes.Input(name="Input")
        to_space = nuke.nodes.OCIOColorSpace(inputs=[inp], in_colorspace="scene_linear", out_colorspace=config.GRADE_LUT_SPACE)
        vf = nuke.nodes.Vectorfield(inputs=[to_space], vfield_file=path)
        back = nuke.nodes.OCIOColorSpace(inputs=[vf], in_colorspace=config.GRADE_LUT_SPACE, out_colorspace="scene_linear")
        nuke.nodes.Output(inputs=[back])
    finally:
        group.end()

    link = nuke.Link_Knob("vfield_file", "LUT file")
    link.makeLink(vf.name(), "vfield_file")
    group.addKnob(link)
    note = nuke.Text_Knob("r2n_grade_note", "", "Resolve's grade for reference. Not for rendering: copy the grade\n"
                                                 "onto the comp in Resolve instead.")
    group.addKnob(note)
    group.setInput(0, source)
    group["label"].setValue(f"Resolve grade (reference)\n{os.path.basename(path)}")
    group["tile_color"].setValue(config.GRADE_GROUP_COLOR)
    group.setXYpos(source.xpos() - 150, source.ypos() + 60)
    logger.info(f"Grade LUT {path} -> {group.name()}")
    return group
