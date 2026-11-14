"""Renders the Resolve2Nuke dialogs offscreen to PNGs, with fake data, for a layout check.

No Nuke session or Resolve needed: it runs in Nuke's bundled Python with a stubbed `nuke`.

    QT_QPA_PLATFORM=offscreen \\
    PYTHONPATH=/opt/Nuke17.1v1/pythonextensions/site-packages:. \\
    LD_LIBRARY_PATH=/opt/Nuke17.1v1 QT_PLUGIN_PATH=/opt/Nuke17.1v1/qtplugins \\
    /opt/Nuke17.1v1/python3 tests/gui_preview.py [output dir]

It looks like Qt's Fusion style in a dark palette, not Nuke's own stylesheet,
so it checks layout, wrapping and label states, not the final look.
"""
import dataclasses
import os
import re
import sys
import types
from unittest import mock

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/r2n_test/gui"
os.makedirs(OUT, exist_ok=True)
os.environ.setdefault("SHOT_NAME", "film_tree_sc010_sh010")

# #---- stub nuke before importing the tool. A real module object, because
# # shiboken's import hook reads module attributes like __name__.
nuke = types.ModuleType("nuke")
nuke.__getattr__ = lambda name: mock.MagicMock(name=f"nuke.{name}")
nuke.GUI = True
nuke.filename = lambda node: "/shots/film/tree/sc010/sh010/renders/comp/c_film_tree_sc010_sh010_comp_v01/c_film_tree_sc010_sh010_comp_v01.%04d.exr"


def _tcl(cmd, text):
    def env(m):
        name = m.group(1)
        if name not in os.environ:
            raise RuntimeError(f"${name}: undefined environment variable")
        return os.environ[name]
    if text.count("[") != text.count("]"):
        raise RuntimeError("missing close-bracket")
    return re.sub(r"\[getenv (\w+)\]", env, text)


nuke.tcl = _tcl
sys.modules["nuke"] = nuke

from Resolve2Nuke.qt import QtCore, QtGui, QtWidgets  # noqa: E402

app = QtWidgets.QApplication([])
app.setStyle("Fusion")
pal = QtGui.QPalette()
for role, colour in [("Window", "#323232"), ("WindowText", "#d0d0d0"), ("Base", "#262626"), ("AlternateBase", "#2c2c2c"),
                     ("Text", "#d0d0d0"), ("Button", "#3c3c3c"), ("ButtonText", "#d0d0d0"), ("Highlight", "#f28d1b"),
                     ("HighlightedText", "#000000")]:
    pal.setColor(getattr(QtGui.QPalette, role), QtGui.QColor(colour))
pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor("#6a6a6a"))
pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor("#6a6a6a"))
pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, QtGui.QColor("#6a6a6a"))
app.setPalette(pal)

from Resolve2Nuke import config, dialogs, resolve_bridge, send_back, stamp  # noqa: E402

config.SETTINGS_ORG = "Resolve2NukeGuiPreview"
QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).clear()

BRAW = resolve_bridge.ShotInfo(
    file_path="/shots/plates/A001_08241708_C025.braw", clip_name="A001_08241708_C025.braw", is_sequence=False,
    width=4096, height=2160, clip_fps=24.0, resolve_colorspace="Blackmagic Design Film Gen 5",
    colorspace_from_project=False, codec="Blackmagic RAW", source_first=107, source_last=145,
    project_name="VFX Test", timeline_name="Timeline 1", timeline_fps=24.0, track_index=1,
    record_in=86400, record_out=86438, record_in_tc="01:00:00:00", item_id="ff639dc7")
RAMP = dataclasses.replace(
    BRAW, clip_name="A010_07261620_C004.braw", source_first=258, source_last=382, speed=189.3,
    retime_frames=[258 + i * 7.17 if i < 45 else 580 + i * 0.8 for i in range(125)], retime_nearest=True)
CANON = dataclasses.replace(
    BRAW, file_path="/shots/plates/IMG_5774.MP4", clip_name="IMG_5774.MP4", width=3840, height=2160, clip_fps=23.976,
    resolve_colorspace="Blackmagic Design Film Gen 5", colorspace_from_project=True, codec="H.265 Main 4:2:2 10 L5.1",
    source_first=512, source_last=537)
CANON_TAGGED = dataclasses.replace(CANON, resolve_colorspace="Canon Cinema Gamut/Canon Log 3", colorspace_from_project=False)


def grab(widget, name):
    widget.show()
    widget.adjustSize()
    for _ in range(3):
        app.processEvents()
    path = os.path.join(OUT, f"{name}.png")
    widget.grab().save(path)
    print("saved", path, f"{widget.width()}x{widget.height()}")
    widget.close()
    app.processEvents()


# #---- pull dialog states
def pull(name, shot=None, error=None, tweak=None):
    def get_shot(resolve=None):
        if error:
            raise resolve_bridge.ResolveError(error)
        return shot
    with mock.patch.object(resolve_bridge, "get_shot", get_shot):
        d = dialogs.PullDialog()
        if tweak:
            tweak(d)
        grab(d, name)


pull("pull_braw", BRAW)
pull("pull_ramp_handles", RAMP, tweak=lambda d: d.handles.setValue(8))
pull("pull_canon_untagged", CANON)
pull("pull_canon_tagged", CANON_TAGGED, tweak=lambda d: (d.set_ocio.setChecked(False), d.create_write.setChecked(False)))
pull("pull_error", error="No clip selected, and no clip with source media under the playhead (01:00:05:00) on an enabled video track.")

# #---- send dialog states
STAMP = stamp.Stamp(project="VFX Test", timeline="Timeline 1", item_id="ff639dc7", record_in_tc="01:00:00:00",
                    resolve_colorspace="Blackmagic Design Film Gen 5", track=1, record_in=86400, record_out=86438,
                    source_first=107, source_last=145, timeline_fps=24.0, retimed=0)


def plan(placement="follow_clip", warnings=(), used=None, reuse=False, track=2):
    return resolve_bridge.PlacementPlan(placement=used or placement, track=track, record_in=86400, record_out=86438,
                                        record_in_tc="01:00:00:00", bin_path="VFX/film_tree_sc010_sh010",
                                        reuse_media=reuse, warnings=list(warnings))


def send(name, missing=(), preview=None, bin_text=None, error=None):
    write = mock.MagicMock()
    write.name.return_value = "Write1"
    info = send_back.SendInfo(write=write, stamp=STAMP, first=107, last=145, missing=list(missing))

    def fake_preview(info_, options):
        if error:
            raise resolve_bridge.ResolveError(error)
        p = preview or plan(options.placement)
        _, fallback = send_back.evaluate_bin(options.bin)
        if fallback:
            p.bin_path = "VFX"
        return p
    with mock.patch.object(send_back, "preview", fake_preview):
        d = dialogs.SendDialog(info)
        if bin_text is not None:
            d.bin.setText(bin_text)
            d._update_preview()
        grab(d, name)


send("send_ready")
send("send_missing", missing=list(range(130, 146)))
send("send_reuse_fallback", preview=plan(used="stored", reuse=True, track=3, warnings=[
    "The original clip isn't in the timeline any more (deleted or replaced), so the comp goes at the position stored when the shot was pulled."]))
send("send_bin_unset", bin_text="VFX/[getenv NOPE]")
send("send_resolve_down", error="Couldn't connect to Resolve.\nMake sure it's running.")

QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).clear()
