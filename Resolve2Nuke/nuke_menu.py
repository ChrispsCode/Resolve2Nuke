"""
Resolve2Nuke.nuke_menu
======================

Builds the Resolve2Nuke Nodes menu. Imported by menu.py at launch, so it only
imports nuke and config; commands are strings that import the tool on click.
"""

from __future__ import annotations

import nuke

from Resolve2Nuke import config


def build() -> None:
    menu = nuke.menu("Nodes").addMenu(config.MENU_NAME, icon=config.ICON_PATH)
    menu.addCommand("Pull shot from Resolve", "__import__('Resolve2Nuke').pull_shot()", icon=config.ICON_PATH)
    menu.addSeparator()
    menu.addCommand("Dev/Reload Resolve2Nuke", "__import__('Resolve2Nuke').reload()")
