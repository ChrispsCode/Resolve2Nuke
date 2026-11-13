"""
Resolve2Nuke.send_back
======================

Sends a rendered comp from a Write back to the Resolve timeline, above the
plate it was pulled from.

The Write's own stamp is the source of truth. Walking up the B pipe to a
stamped Read is only a fallback, for Writes that weren't made by the pull.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import nuke

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke import resolve_bridge
from Resolve2Nuke import stamp as stamp_mod


class SendError(RuntimeError):
    """A problem on the Nuke side, with a message meant for the artist."""


@dataclass
class SendOptions:
    placement: str = config.DEFAULT_PLACEMENT


# #----------------------------------------------------------------------------
# # Nuke side

def find_write() -> nuke.Node:
    """The selected Write, or the only Write in the script."""
    selected = [n for n in nuke.selectedNodes() if n.Class() == "Write"]
    if len(selected) == 1:
        return selected[0]
    if len(selected) > 1:
        raise SendError("More than one Write is selected. Select just the one to send.")
    writes = nuke.allNodes("Write")
    if len(writes) == 1:
        return writes[0]
    if not writes:
        raise SendError("There's no Write in the script.")
    raise SendError(f"There are {len(writes)} Writes in the script. Select the one to send.")


def find_stamp(write: nuke.Node) -> tuple[stamp_mod.Stamp, list[str]]:
    """The stamp to send with, plus any warnings about it."""
    warnings = []
    own = stamp_mod.read_stamp(write)
    upstream, upstream_node = stamp_mod.find_upstream_stamp(write)
    if own and upstream and own.item_id != upstream.item_id:
        warnings.append(f"{write.name()} and {upstream_node.name()} came from different Resolve clips. "
                        f"Using {write.name()}'s.")
    if own:
        return own, warnings
    if upstream:
        logger.info(f"{write.name()} has no stamp, using {upstream_node.name()}'s")
        return upstream, warnings
    raise SendError(f"{write.name()} has no Resolve2Nuke stamp, and there's no stamped Read up its B pipe.\n"
                    f"Pull the shot with Resolve2Nuke first.")


def frame_range(write: nuke.Node) -> tuple[int, int]:
    """The Write's own range when limited, else the script's."""
    if write["use_limit"].value():
        return int(write["first"].value()), int(write["last"].value())
    root = nuke.root()
    return int(root["first_frame"].value()), int(root["last_frame"].value())


def missing_frames(write: nuke.Node, first: int, last: int) -> list[int]:
    return [f for f in range(first, last + 1) if not os.path.isfile(write["file"].evaluate(f))]


def _format_frames(frames: list[int]) -> str:
    """[107, 108, 109, 120] -> '107-109, 120'"""
    runs, start, prev = [], frames[0], frames[0]
    for f in frames[1:] + [None]:
        if f is not None and f == prev + 1:
            prev = f
            continue
        runs.append(f"{start}-{prev}" if start != prev else str(start))
        if f is not None:
            start = prev = f
    return ", ".join(runs)


# #----------------------------------------------------------------------------
# # Entry point

def send(write: nuke.Node | None = None, options: SendOptions | None = None) -> resolve_bridge.PlaceResult:
    """Check the render, then place it in Resolve. Raises SendError / ResolveError."""
    options = options or SendOptions()
    write = write or find_write()
    if write["file_type"].value() != "exr":
        raise SendError(f"{write.name()} isn't writing EXRs.")

    stamp, warnings = find_stamp(write)
    first, last = frame_range(write)
    missing = missing_frames(write, first, last)
    if missing:
        raise SendError(f"{write.name()} is missing {len(missing)} of {last - first + 1} frames "
                        f"({_format_frames(missing)}).\nRender {first}-{last} first.")

    comp = resolve_bridge.CompPlacement(
        sequence_path=nuke.filename(write),
        first=first,
        last=last,
        project=stamp.project,
        timeline=stamp.timeline,
        item_id=stamp.item_id,
        track=stamp.track,
        record_in=stamp.record_in,
        source_first=stamp.source_first,
    )
    result = resolve_bridge.place_comp(comp, options.placement)
    result.warnings[:0] = warnings
    return result
