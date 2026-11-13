"""
Resolve2Nuke
============

Pulls the selected (or playhead) clip from DaVinci Resolve into Nuke, with the
script's colour, fps, format and frame range set to match.

Script Editor:
    import Resolve2Nuke; Resolve2Nuke.pull_shot()
"""

from __future__ import annotations

__version__ = "0.2.0"


def pull_shot(options=None):
    """Menu entry point. Shows a dialog for anything the artist must not miss."""
    import nuke

    from Resolve2Nuke import logger, resolve_bridge, shot_build

    try:
        shot = resolve_bridge.get_shot()
    except resolve_bridge.ResolveError as e:
        logger.error(str(e))
        nuke.message(f"Resolve2Nuke:\n\n{e}")
        return None

    result = shot_build.build(shot, options)

    if result.unknown_colorspace:
        msg = (f"Unknown colour space: '{shot.resolve_colorspace}'\n\n"
               f"{result.read.name()} was left on its default colorspace, so it probably "
               f"won't match Resolve. Set it by hand, and add the mapping to "
               f"Resolve2Nuke config.RESOLVE_TO_OCIO once it's been tested.")
        logger.warning(msg.replace("\n\n", " "))
        nuke.message(f"Resolve2Nuke:\n\n{msg}")

    logger.info(f"Created {result.read.name()}" + (f" and {result.write.name()}" if result.write else ""))
    return result


def send_comp(options=None):
    """Menu entry point: send the selected (or only) Write's render to Resolve."""
    import nuke

    from Resolve2Nuke import logger, resolve_bridge, send_back as send_mod

    try:
        result = send_mod.send(options=options)
    except (send_mod.SendError, resolve_bridge.ResolveError) as e:
        logger.error(str(e))
        nuke.message(f"Resolve2Nuke:\n\n{e}")
        return None

    summary = (f"Placed {result.clip_name}\non V{result.track} at {result.record_in_tc} "
               f"({result.record_out - result.record_in + 1} frames, {result.placement.replace('_', ' ')}).")
    if result.reused_media:
        summary += "\n\nReused the clip already in the media pool."
    if result.warnings:
        for w in result.warnings:
            logger.warning(w)
        summary += "\n\nWarnings:\n- " + "\n- ".join(result.warnings)
    nuke.message(f"Resolve2Nuke:\n\n{summary}")
    return result


def reload() -> None:
    """Reload the package from disk (for development)."""
    from Resolve2Nuke.dev.reload_tool import reload_resolve2nuke
    reload_resolve2nuke()
