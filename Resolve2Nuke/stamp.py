"""
Resolve2Nuke.stamp
==================

The Resolve2Nuke tab: where a plate came from in Resolve. Added to the Read
and the Write at pull time, read back by the send-back step.

The knob names (config.KNOB_PREFIX + field) are a public API: saved scripts
carry them, so keep them stable.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import nuke

from Resolve2Nuke import config
from Resolve2Nuke.resolve_bridge import ShotInfo


@dataclass
class Stamp:
    project: str
    timeline: str
    item_id: str
    record_in_tc: str
    resolve_colorspace: str
    track: int
    record_in: int
    record_out: int
    source_first: int
    source_last: int
    timeline_fps: float
    retimed: int            # 1 when the Read is retimed by a Kronos (frames are output frames)

    @classmethod
    def from_shot(cls, shot: ShotInfo) -> Stamp:
        return cls(
            project=shot.project_name,
            timeline=shot.timeline_name,
            item_id=shot.item_id,
            record_in_tc=shot.record_in_tc,
            resolve_colorspace=shot.resolve_colorspace,
            track=shot.track_index,
            record_in=shot.record_in,
            record_out=shot.record_out,
            source_first=shot.source_first,
            source_last=shot.source_last,
            timeline_fps=shot.timeline_fps,
            retimed=int(shot.retimed),
        )


# # field -> knob label, in display order
_LABELS = {
    "project": "project",
    "timeline": "timeline",
    "item_id": "item id",
    "record_in_tc": "record in TC",
    "resolve_colorspace": "Resolve colour space",
    "track": "video track",
    "record_in": "record in",
    "record_out": "record out",
    "source_first": "source first",
    "source_last": "source last",
    "timeline_fps": "timeline fps",
    "retimed": "retimed",
}

# # Field types are strings here (from __future__ import annotations)
_KNOB_CLASSES = {"str": nuke.String_Knob, "int": nuke.Int_Knob, "float": nuke.Double_Knob}
_CASTS = {"str": str, "int": int, "float": float}


def _knob_name(field: str) -> str:
    return config.KNOB_PREFIX + field


def write_stamp(node: nuke.Node, stamp: Stamp) -> None:
    """Add (or refresh) the Resolve2Nuke tab on a node."""
    if config.KNOB_TAB not in node.knobs():
        node.addKnob(nuke.Tab_Knob(config.KNOB_TAB, "Resolve2Nuke"))
    types = {f.name: f.type for f in fields(Stamp)}
    for field, label in _LABELS.items():
        name = _knob_name(field)
        knob = node.knobs().get(name)
        if knob is None:
            knob = _KNOB_CLASSES[types[field]](name, label)
            knob.setEnabled(False)
            node.addKnob(knob)
        knob.setValue(getattr(stamp, field))


def read_stamp(node: nuke.Node) -> Stamp | None:
    """The node's stamp, or None if it hasn't got a complete one."""
    knobs = node.knobs()
    values = {}
    for f in fields(Stamp):
        knob = knobs.get(_knob_name(f.name))
        if knob is None:
            return None
        values[f.name] = _CASTS[f.type](knob.value())
    return Stamp(**values)


def find_upstream_stamp(node: nuke.Node) -> tuple[Stamp, nuke.Node] | tuple[None, None]:
    """Walk up the B pipe (input 0) to the first node with a stamp."""
    current = node.input(0)
    seen = set()
    while current is not None and current.name() not in seen:
        seen.add(current.name())
        found = read_stamp(current)
        if found:
            return found, current
        current = current.input(0)
    return None, None
