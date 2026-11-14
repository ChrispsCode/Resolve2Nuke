"""
Resolve2Nuke
============

Pulls the selected (or playhead) clip from DaVinci Resolve into Nuke, with the
script's colour, fps, format and frame range set to match.

Script Editor:
    import Resolve2Nuke; Resolve2Nuke.pull_shot()
"""

from __future__ import annotations

__version__ = "0.4.0"


def pull_shot(options=None):
    """Menu entry point. In the GUI, opens the pull dialog. With options (or
    headless), pulls straight away."""
    import nuke

    if options is None and nuke.GUI:
        from Resolve2Nuke import dialogs
        dialogs.open_pull_dialog()
        return None

    from Resolve2Nuke import actions, resolve_bridge

    try:
        shot = resolve_bridge.get_shot()
    except resolve_bridge.ResolveError as e:
        actions.report_error(e)
        return None
    return actions.run_pull(shot, options)


def send_comp(options=None):
    """Menu entry point. In the GUI, opens the send dialog. With options (or
    headless), sends the selected (or only) Write straight away."""
    import nuke

    if options is None and nuke.GUI:
        from Resolve2Nuke import dialogs
        dialogs.open_send_dialog()
        return None

    from Resolve2Nuke import actions
    return actions.run_send(options)


def reload() -> None:
    """Reload the package from disk (for development)."""
    from Resolve2Nuke.dev.reload_tool import reload_resolve2nuke
    reload_resolve2nuke()
