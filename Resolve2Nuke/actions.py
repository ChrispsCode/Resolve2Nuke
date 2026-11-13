"""
Resolve2Nuke.actions
====================

Runs a pull or a send and reports the outcome with Nuke dialogs. Shared by
the dialogs and the no-dialog entry points, so both behave the same.
"""

from __future__ import annotations

import nuke

from Resolve2Nuke import logger
from Resolve2Nuke import resolve_bridge
from Resolve2Nuke import send_back
from Resolve2Nuke import shot_build


def _message(text: str) -> None:
    nuke.message(f"Resolve2Nuke:\n\n{text}")


def report_error(e: Exception) -> None:
    logger.error(str(e))
    _message(str(e))


def run_pull(shot: resolve_bridge.ShotInfo, options: shot_build.ShotOptions | None = None) -> shot_build.BuildResult:
    result = shot_build.build(shot, options)
    if result.unknown_colorspace:
        msg = (f"Unknown colour space: '{shot.resolve_colorspace}'\n\n"
               f"{result.read.name()} was left on its default colorspace, so it probably "
               f"won't match Resolve. Set it by hand, and add the mapping to "
               f"Resolve2Nuke config.RESOLVE_TO_OCIO once it's been tested.")
        logger.warning(msg.replace("\n\n", " "))
        _message(msg)
    logger.info(f"Created {result.read.name()}" + (f" and {result.write.name()}" if result.write else ""))
    return result


def run_send(options: send_back.SendOptions | None = None, write: nuke.Node | None = None,
             ) -> resolve_bridge.PlaceResult | None:
    """Send, then show a summary. Errors become a dialog and return None."""
    try:
        result = send_back.send(write=write, options=options)
    except (send_back.SendError, resolve_bridge.ResolveError) as e:
        report_error(e)
        return None

    summary = (f"Placed {result.clip_name}\non V{result.track} at {result.record_in_tc} "
               f"({result.record_out - result.record_in + 1} frames, {result.placement.replace('_', ' ')}),\n"
               f"bin {result.bin_path}.")
    if result.reused_media:
        summary += "\n\nReused the clip already in that bin."
    if result.warnings:
        for w in result.warnings:
            logger.warning(w)
        summary += "\n\nWarnings:\n- " + "\n- ".join(result.warnings)
    _message(summary)
    return result
