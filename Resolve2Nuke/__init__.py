"""
Resolve2Nuke
============

Pulls the selected (or playhead) clip from DaVinci Resolve into Nuke, with the
script's colour, fps, format and frame range set to match.

Script Editor:
    import Resolve2Nuke; Resolve2Nuke.pull_shot()
"""

from __future__ import annotations

__version__ = "0.1.0"


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


def reload() -> None:
    """Reload the package from disk (for development)."""
    from Resolve2Nuke.dev.reload_tool import reload_resolve2nuke
    reload_resolve2nuke()
