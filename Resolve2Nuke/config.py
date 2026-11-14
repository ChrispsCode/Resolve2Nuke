"""
Resolve2Nuke.config
===================

Constants for pulling shots from DaVinci Resolve into Nuke.

The colour settings below were verified by pixel test on 2026-11-12 (Resolve
21.1, Nuke 17.1v1): an EXR written with these settings sits on the Resolve
timeline over its BRAW with a median difference of 0.0006 in DaVinci
Intermediate, with no colour space transforms or bypass in Resolve.
"""

from __future__ import annotations

import logging
import os
import platform

# #----------------------------------------------------------------------------
# # Logging

# LOG_LEVEL = logging.DEBUG
LOG_LEVEL = logging.INFO

# # "full"  - [r2n.module.Class.function] LEVEL: message (development)
# # "short" - [r2n] LEVEL: message
LOG_PREFIX_STYLE = "full"

# #----------------------------------------------------------------------------
# # Resources

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PACKAGE_DIR, "resources", "DaVinci_Resolve_Studio_icon.png")
MENU_NAME = "Resolve2Nuke"

# #----------------------------------------------------------------------------
# # Resolve scripting API locations, per platform (Resolve's documented defaults)

_SYSTEM = platform.system()
if _SYSTEM == "Windows":
    RESOLVE_SCRIPT_API = os.path.expandvars(r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting")
    RESOLVE_SCRIPT_LIB = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
elif _SYSTEM == "Darwin":
    RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    RESOLVE_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
else:
    RESOLVE_SCRIPT_API = "/opt/resolve/Developer/Scripting"
    RESOLVE_SCRIPT_LIB = "/opt/resolve/libs/Fusion/fusionscript.so"

# #----------------------------------------------------------------------------
# # Colour

# # Set on the root by name only. Its values() entries are "name\tACES/name",
# # and passing that whole string silently keeps the old config.
OCIO_CONFIG = "fn-nuke_studio-config-v2.2.0_aces-v1.3_ocio-v2.4"

# # Resolve input colour space (clip property, or the project's default when
# # the clip says "Project") -> OCIO colourspace for the Read.
# # Only add entries that have been pixel tested.
RESOLVE_TO_OCIO = {
    "Blackmagic Design Film Gen 5": "BMDFilm WideGamut Gen5",
}

# # BRAW decode. Camera Metadata matches Resolve's decode when the clip's
# # Camera Raw panel is on Camera Metadata (or Project, with the project on it).
BRAW_DECODE_USING = "Camera Metadata"

# #----------------------------------------------------------------------------
# # Write

WRITE_FILE = "[getenv SHOT_ROOT]/renders/comp/c_[getenv SHOT_NAME]_comp_v01/c_[getenv SHOT_NAME]_comp_v01.%04d.exr"
WRITE_COLORSPACE = "BMDFilm WideGamut Gen5"
# # The same colour space by Resolve's name. Sent comps are tagged with it, so
# # they don't depend on the project's input colour space.
WRITE_RESOLVE_COLORSPACE = "Blackmagic Design Film Gen 5"
WRITE_DATATYPE = "16 bit half"

# #----------------------------------------------------------------------------
# # Template plate (Resolve renders the clip with its grade, sizing and retime)

# # Resolve only renders into its Media Storage locations, so it renders here
# # and the pull moves the result into the shot. Must be inside a mapped
# # Media Storage folder (Resolve Preferences > System > Media Storage).
TEMPLATE_STAGING_DIR = os.path.expanduser("~/DaVinci Resolve Media/Resolve2Nuke")

# # Destinations, under $SHOT_ROOT. {name} is "<SHOT_NAME>_plate_v##".
TEMPLATE_DIR = "plates"
TEMPLATE_SEQUENCE = "{name}/{name}.%04d.exr"
TEMPLATE_MOVIE = "{name}.mov"
TEMPLATE_NAME = "{shot}_plate_v{version:02d}"

# # key -> (label, Resolve format, Resolve codec, is a movie)
TEMPLATE_FORMATS = {
    "exr": ("EXR (half, ZIP: lossless)", "exr", "RGBHalfZIP", False),
    "prores": ("ProRes 4444 XQ (12-bit, about 1/3 the size)", "mov", "ProRes4444XQ", True),
}
DEFAULT_TEMPLATE_FORMAT = "exr"

# # Rendered in the same encoding as the Writes, so a template plate reads like
# # the hero BRAW. Verified 2026-11-14: Resolve's BMD Film Gen 5 plate of an
# # ungraded BRAW matches Nuke's own BRAW decode to a median of 0.0006 (EXR
# # and ProRes 4444 XQ). Set on the project for the render, then restored.
TEMPLATE_RESOLVE_OUTPUT = "Blackmagic Design Film Gen 5"
TEMPLATE_COLORSPACE = "BMDFilm WideGamut Gen5"

# #----------------------------------------------------------------------------
# # Send back

# # Media pool bin for rendered comps. TCL is evaluated, "/" makes nested bins.
# # If it can't be evaluated (e.g. not hozed, so SHOT_NAME is unset) it falls back.
DEFAULT_BIN = "VFX/[getenv SHOT_NAME]"
FALLBACK_BIN = "VFX"

# # Default placement: "follow_clip" (find the original clip wherever it is now,
# # align by source frame), "stored" (where it was at pull time) or "playhead"
DEFAULT_PLACEMENT = "follow_clip"

# #----------------------------------------------------------------------------
# # Dialogs

# # Remember the last-used fps source, handles, placement and bin per user
# # (QSettings). The checkboxes always start ticked, so a secondary-plate pull
# # never leaves the next main-plate pull half set up.
REMEMBER_SETTINGS = True
SETTINGS_ORG = "Resolve2Nuke"
SETTINGS_APP = "Resolve2Nuke"

# #----------------------------------------------------------------------------
# # Knobs added to the Read to remember where it came from in Resolve.
# # These names are a public API: the send-back step reads them from saved scripts.

KNOB_TAB = "r2n_tab"
KNOB_PREFIX = "r2n_"
