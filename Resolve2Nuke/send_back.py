"""
Resolve2Nuke.send_back
======================

Sends a rendered comp from a Write back to the Resolve timeline, above the
plate it was pulled from.

The Write's own stamp is the source of truth. Walking up the B pipe to a
stamped Read is only a fallback, for Writes that weren't made by the pull.

Everything here is Qt-free so it can be tested headless. The send dialog is
a thin layer over gather(), preview() and send().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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
    bin: str = config.DEFAULT_BIN   # TCL, evaluated at send time


@dataclass
class SendInfo:
    """Everything about a Write that the send needs, gathered without touching Resolve."""
    write: nuke.Node
    stamp: stamp_mod.Stamp
    first: int
    last: int
    missing: list[int]
    warnings: list[str] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return self.last - self.first + 1

    def comp(self) -> resolve_bridge.CompPlacement:
        return resolve_bridge.CompPlacement(
            sequence_path=nuke.filename(self.write),
            first=self.first,
            last=self.last,
            project=self.stamp.project,
            timeline=self.stamp.timeline,
            item_id=self.stamp.item_id,
            track=self.stamp.track,
            record_in=self.stamp.record_in,
            source_first=self.stamp.source_first,
            retimed=bool(self.stamp.retimed),
        )


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


def format_frames(frames: list[int]) -> str:
    """[107, 108, 109, 120] -> '107-109, 120'"""
    if not frames:
        return ""
    runs, start, prev = [], frames[0], frames[0]
    for f in frames[1:] + [None]:
        if f is not None and f == prev + 1:
            prev = f
            continue
        runs.append(f"{start}-{prev}" if start != prev else str(start))
        if f is not None:
            start = prev = f
    return ", ".join(runs)


def evaluate_bin(text: str) -> tuple[str, str | None]:
    """(bin path, why it fell back or None). TCL like [getenv SHOT_NAME] is evaluated."""
    try:
        path = "/".join(resolve_bridge.split_bin_path(nuke.tcl("subst", text)))
    except RuntimeError as e:
        return config.FALLBACK_BIN, f"couldn't evaluate '{text}' ({e})"
    if not path:
        return config.FALLBACK_BIN, f"'{text}' evaluated to nothing"
    return path, None


def gather(write: nuke.Node | None = None) -> SendInfo:
    """Find the Write and check its render. Raises SendError when there's nothing sendable."""
    write = write or find_write()
    if write["file_type"].value() != "exr":
        raise SendError(f"{write.name()} isn't writing EXRs.")
    stamp, warnings = find_stamp(write)
    first, last = frame_range(write)
    return SendInfo(write=write, stamp=stamp, first=first, last=last,
                    missing=missing_frames(write, first, last), warnings=warnings)


# #----------------------------------------------------------------------------
# # Entry points

def preview(info: SendInfo, options: SendOptions) -> resolve_bridge.PlacementPlan:
    """Where the comp would land. Changes nothing in Resolve."""
    bin_path, fallback = evaluate_bin(options.bin)
    plan = resolve_bridge.plan_placement(info.comp(), options.placement, bin_path)
    if fallback:
        plan.warnings.insert(0, f"Bin {fallback}, so using '{bin_path}'.")
    plan.warnings[:0] = info.warnings
    return plan


def send(write: nuke.Node | None = None, options: SendOptions | None = None,
         info: SendInfo | None = None) -> resolve_bridge.PlaceResult:
    """Check the render, then place it in Resolve. Raises SendError / ResolveError."""
    options = options or SendOptions()
    info = info or gather(write)
    if info.missing:
        raise SendError(f"{info.write.name()} is missing {len(info.missing)} of {info.frame_count} frames "
                        f"({format_frames(info.missing)}).\nRender {info.first}-{info.last} first.")

    bin_path, fallback = evaluate_bin(options.bin)
    result = resolve_bridge.place_comp(info.comp(), options.placement, bin_path)
    if fallback:
        result.warnings.insert(0, f"Bin {fallback}, so used '{bin_path}'.")
    result.warnings[:0] = info.warnings
    return result
