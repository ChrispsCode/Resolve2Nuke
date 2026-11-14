"""
Resolve2Nuke.dialogs
====================

The pull and send dialogs. Thin Qt layers over shot_build / send_back /
actions: all the logic lives in those Qt-free modules so it can be tested
headless.

Windows are non-modal (show(), not exec), one per type, and must set
WA_DeleteOnClose so closing really deletes them (see _register).
"""

from __future__ import annotations

import textwrap

import nuke

from Resolve2Nuke import actions
from Resolve2Nuke import config
from Resolve2Nuke import resolve_bridge
from Resolve2Nuke import send_back
from Resolve2Nuke import settings
from Resolve2Nuke import shot_build
from Resolve2Nuke import template_plate
from Resolve2Nuke.qt import QtCore, QtGui, QtWidgets

GREEN = "#7cc47c"
RED = "#e06c6c"
ORANGE = "#e3a14a"
GREY = "#8a8a8a"

PLACEMENT_LABELS = {
    "follow_clip": "Follow clip (find the original clip, align by source frame)",
    "stored": "Stored position (where it was when pulled)",
    "playhead": "Playhead (first frame at Resolve's playhead)",
}

# #----------------------------------------------------------------------------
# # Window registry (TMM's launchers pattern)

_OPEN_WINDOWS: dict[str, QtWidgets.QWidget] = {}


def _register(name: str, widget: QtWidgets.QWidget) -> bool:
    """Track widget as the single open instance. False if one is already open (it's raised instead)."""
    existing = _OPEN_WINDOWS.get(name)
    if existing is not None:
        try:
            existing.isVisible()
        except RuntimeError:
            existing = None
    if existing is not None:
        existing.raise_()
        existing.activateWindow()
        return False
    widget.destroyed.connect(lambda: _OPEN_WINDOWS.pop(name, None))
    _OPEN_WINDOWS[name] = widget
    return True


def _show(name: str, cls, *args) -> None:
    existing = _OPEN_WINDOWS.get(name)
    if existing is not None:
        try:
            existing.raise_()
            existing.activateWindow()
            return
        except RuntimeError:
            _OPEN_WINDOWS.pop(name, None)
    win = cls(*args, parent=QtWidgets.QApplication.activeWindow())
    if _register(name, win):
        win.show()
        win.raise_()
        win.activateWindow()


WRAP_CHARS = 70


def _html(text: str, color: str | None = None, bold: bool = False, wrap: bool = False) -> str:
    """Escape for a rich-text label. wrap=True breaks long text into lines here,
    because QLabel's own word wrap mis-sizes in form layouts (it clipped in Nuke)."""
    if wrap:
        text = "\n".join(textwrap.fill(line, WRAP_CHARS) for line in text.split("\n"))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    if bold:
        text = f"<b>{text}</b>"
    return f'<span style="color: {color};">{text}</span>' if color else text


def _label() -> QtWidgets.QLabel:
    label = QtWidgets.QLabel()
    label.setTextFormat(QtCore.Qt.RichText)
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    # # No word wrap: it mis-sizes in form layouts. Long text is broken by _html(wrap=True).
    return label


def _path_label() -> QtWidgets.QLabel:
    """A label for a full path that never widens the window: the text runs off
    the right edge and shows in full when the window is made wider."""
    label = _label()
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
    label.setSizePolicy(policy)
    return label


class _Dialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Resolve2Nuke - {title}")
        self.setWindowIcon(QtGui.QIcon(config.ICON_PATH))
        self.setMinimumWidth(560)
        self.layout_ = QtWidgets.QVBoxLayout(self)

        self.summary = QtWidgets.QFormLayout()
        self.summary.setLabelAlignment(QtCore.Qt.AlignRight)
        box = QtWidgets.QGroupBox("Shot")
        box.setLayout(self.summary)
        self.layout_.addWidget(box)

    def _buttons(self, action_text: str) -> None:
        # # Spare height goes here, not into the group boxes.
        self.layout_.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("↻ Refresh")
        self.refresh_button.setToolTip("Read Resolve and the script again")
        self.refresh_button.clicked.connect(self.refresh)
        self.action_button = QtWidgets.QPushButton(action_text)
        self.action_button.setDefault(True)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.close)
        row.addWidget(self.refresh_button)
        row.addStretch()
        row.addWidget(self.action_button)
        row.addWidget(self.cancel_button)
        self.layout_.addLayout(row)

    def refresh(self) -> None:
        raise NotImplementedError

    def fit_height(self) -> None:
        """Fit the text after it changes: grow wider if needed (never narrower,
        so the window doesn't jump about), and take exactly the height it needs."""
        self.layout_.invalidate()
        self.layout_.activate()
        hint = self.sizeHint()
        width = max(self.width() if self.isVisible() else 0, hint.width(), self.minimumWidth())
        self.resize(width, hint.height())


# #----------------------------------------------------------------------------
# # Pull

class PullDialog(_Dialog):
    def __init__(self, parent=None):
        super().__init__("Pull shot", parent)
        self._shot: resolve_bridge.ShotInfo | None = None
        defaults = settings.load_shot_options()

        (self.clip_label, self.position_label, self.source_label, self.retime_label, self.colour_label,
         self.fps_label) = (_label() for _ in range(6))
        self.summary.addRow("Clip:", self.clip_label)
        self.summary.addRow("Timeline:", self.position_label)
        self.summary.addRow("Source:", self.source_label)
        self.summary.addRow("Retime:", self.retime_label)
        self.summary.addRow("Colour:", self.colour_label)
        self.summary.addRow("FPS:", self.fps_label)

        box = QtWidgets.QGroupBox("Script settings")
        grid = QtWidgets.QGridLayout(box)
        self.set_ocio = QtWidgets.QCheckBox("Set OCIO config")
        self.set_ocio.setToolTip(config.OCIO_CONFIG)
        self.set_fps = QtWidgets.QCheckBox("Set fps from")
        self.fps_source = QtWidgets.QComboBox()
        self.fps_source.addItem("Timeline", "timeline")
        self.fps_source.addItem("Clip", "clip")
        self.fps_source.setCurrentIndex(max(0, self.fps_source.findData(defaults.fps_source)))
        self.set_format = QtWidgets.QCheckBox("Set format")
        self.set_frame_range = QtWidgets.QCheckBox("Set frame range, handles")
        self.handles = QtWidgets.QSpinBox()
        self.handles.setRange(0, 9999)
        self.handles.setValue(defaults.handles)
        self.create_write = QtWidgets.QCheckBox("Create Write")
        self.create_write.setToolTip(config.WRITE_FILE)
        self.include_grade = QtWidgets.QCheckBox("Include grade (LUT)")
        self.include_grade.setToolTip("Exports the clip's Resolve grade as a LUT and adds a ResolveGrade branch\n"
                                      "off the plate, for reference. It isn't in the Write's pipe.")
        checkboxes = (self.set_ocio, self.set_fps, self.set_format, self.set_frame_range, self.include_grade, self.create_write)
        for box_ in checkboxes:
            box_.setChecked(True)  # # always ticked: the safe main-plate case (grade: unticked when not hozed)

        grid.addWidget(self.set_ocio, 0, 0, 1, 2)
        grid.addWidget(self.set_fps, 1, 0)
        grid.addWidget(self.fps_source, 1, 1)
        grid.addWidget(self.set_format, 2, 0, 1, 2)
        grid.addWidget(self.set_frame_range, 3, 0)
        grid.addWidget(self.handles, 3, 1)
        grid.addWidget(self.include_grade, 4, 0, 1, 2)
        grid.addWidget(self.create_write, 5, 0, 1, 2)
        grid.setColumnStretch(2, 1)
        self.layout_.addWidget(box)

        # # Template plate: Resolve renders the clip with its effects (off by default, hozed only).
        tbox = QtWidgets.QGroupBox("Template plate")
        tgrid = QtWidgets.QGridLayout(tbox)
        self.render_template = QtWidgets.QCheckBox("Render a template plate from Resolve")
        self.render_template.setToolTip("Resolve renders the clip as it plays in the edit (grade, sizing, retime),\n"
                                        "encoded as BMD Film Gen 5, into the shot's plates folder.")
        self.template_format = QtWidgets.QComboBox()
        for key, (label, *_rest) in config.TEMPLATE_FORMATS.items():
            self.template_format.addItem(label, key)
        self.template_format.setCurrentIndex(max(0, self.template_format.findData(defaults.template_format)))
        self.template_clip_res = QtWidgets.QCheckBox("At the clip's resolution (otherwise the timeline's)")
        self.template_clip_res.setChecked(defaults.template_clip_res)
        self.template_label = _path_label()
        tgrid.addWidget(self.render_template, 0, 0, 1, 2)
        tgrid.addWidget(QtWidgets.QLabel("Format"), 1, 0)
        tgrid.addWidget(self.template_format, 1, 1)
        tgrid.addWidget(self.template_clip_res, 2, 0, 1, 2)
        tgrid.addWidget(self.template_label, 3, 0, 1, 2)
        tgrid.setColumnStretch(1, 1)
        self.layout_.addWidget(tbox)

        self._buttons("Pull")
        self.render_template.toggled.connect(self._update_enabled)
        self.include_grade.toggled.connect(self._update_enabled)
        self.template_format.activated.connect(self._update_enabled)
        self.template_clip_res.toggled.connect(self._update_enabled)
        self.action_button.clicked.connect(self._pull)
        # # Connected after the buttons exist: _update_enabled touches action_button.
        for box_ in checkboxes:
            box_.toggled.connect(self._update_enabled)
        self.handles.valueChanged.connect(self._update_labels)
        self.fps_source.activated.connect(self._update_labels)
        self.refresh()

    def options(self) -> shot_build.ShotOptions:
        return shot_build.ShotOptions(
            set_ocio=self.set_ocio.isChecked(),
            set_fps=self.set_fps.isChecked(),
            fps_source=self.fps_source.currentData(),
            set_format=self.set_format.isChecked(),
            set_frame_range=self.set_frame_range.isChecked(),
            handles=self.handles.value(),
            create_write=self.create_write.isChecked(),
            include_grade=self.include_grade.isChecked() and self.include_grade.isEnabled(),
            render_template=self.render_template.isChecked() and self.render_template.isEnabled(),
            template_format=self.template_format.currentData(),
            template_clip_res=self.template_clip_res.isChecked(),
        )

    def refresh(self) -> None:
        try:
            self._shot = resolve_bridge.get_shot()
        except resolve_bridge.ResolveError as e:
            self._shot = None
            self.clip_label.setText(_html(str(e), RED, wrap=True))
            for label in (self.position_label, self.source_label, self.retime_label, self.colour_label, self.fps_label):
                label.setText("")
        self._update_labels()
        self._update_enabled()

    def _update_labels(self) -> None:
        shot = self._shot
        if shot is None:
            return
        self.clip_label.setText(_html(shot.clip_name, bold=True) + _html(f"   {shot.width}x{shot.height}", GREY))
        self.position_label.setText(_html(f"{shot.timeline_name}, V{shot.track_index} at {shot.record_in_tc}")
                                    + _html(f"   ({shot.project_name})", GREY))
        handles = self.handles.value() if self.set_frame_range.isChecked() else 0
        rng = f"{shot.source_first}-{shot.source_last} ({shot.duration} frames)"
        if shot.retimed:
            rng += _html("  ·  output frames", GREY)
        if handles:
            rng += _html(f"  ·  script range {shot.source_first - handles}-{shot.source_last + handles}", GREY)
        self.source_label.setText(rng)

        if shot.retimed:
            lo, hi = int(min(shot.retime_frames)), int(max(shot.retime_frames))
            kind = f"speed ramp (average {shot.speed:.0f}%)" if shot.is_ramp else f"{shot.speed:g}%"
            mode = "nearest frame" if shot.retime_nearest else "blended"
            self.retime_label.setText(_html(f"{kind}, source {lo}-{hi}", ORANGE) + "<br>"
                                      + _html(f"→ Kronos (Frame), a key on every frame, {mode}", GREY))
        else:
            self.retime_label.setText(_html("none", GREY))

        ocio = config.RESOLVE_TO_OCIO.get(shot.resolve_colorspace)
        if ocio:
            text = _html(f"{shot.resolve_colorspace}  →  {ocio}")
        else:
            text = _html(f"{shot.resolve_colorspace}  →  unknown, the Read will need setting by hand", RED)
        tag_warning = shot.colour_tag_warning()
        if tag_warning:
            text += "<br>" + _html(tag_warning, ORANGE, wrap=True)
        self.colour_label.setText(text)

        if abs(shot.clip_fps - shot.timeline_fps) < 0.001:
            self.fps_label.setText(f"{shot.timeline_fps:g}")
        elif shot.retimed:
            self.fps_label.setText(_html(f"clip {shot.clip_fps:g}, timeline {shot.timeline_fps:g}", ORANGE))
        elif shot.frame_for_frame:
            self.fps_label.setText(f"timeline {shot.timeline_fps:g}" + _html(
                f"   clip {shot.clip_fps:g}, played frame-for-frame (1:1)", GREY))
        else:
            self.fps_label.setText(_html(f"clip {shot.clip_fps:g}, timeline {shot.timeline_fps:g}: Resolve isn't "
                                         f"playing it frame-for-frame, so frames won't map 1:1", ORANGE, wrap=True))
        # # Re-fit: wrapped labels change height with their text.
        self.fit_height()

    def _update_enabled(self) -> None:
        shot = self._shot
        fps_differ = bool(shot and abs(shot.clip_fps - shot.timeline_fps) >= 0.001)
        self.fps_source.setEnabled(self.set_fps.isChecked() and fps_differ)
        self.handles.setEnabled(self.set_frame_range.isChecked())
        hozed = template_plate.hoz_context() is not None
        self.include_grade.setEnabled(hozed)
        if not hozed:
            self.include_grade.setChecked(False)
        self.include_grade.setToolTip(
            "Exports the clip's Resolve grade as a LUT (Resolve flicks to the Color page and back)\n"
            "and adds a ResolveGrade branch off the plate, for reference. It isn't in the Write's pipe."
            if hozed else "Needs a hoz context, so the LUT lands in the right shot.")
        self._update_template()
        self.action_button.setEnabled(shot is not None)
        self._update_labels()

    def _update_template(self) -> None:
        """Hozed only; shows the exact path the plate will get."""
        try:
            plate = template_plate.target(self.template_format.currentData())
        except template_plate.TemplateError as e:
            self.render_template.setEnabled(False)
            self.render_template.setChecked(False)
            self.template_label.setText(_html(str(e), GREY, wrap=True))
        else:
            self.render_template.setEnabled(True)
            on = self.render_template.isChecked()
            text = _html(f"→ {plate.path}", None if on else GREY)
            if on and self._shot is not None:
                size = f"{self._shot.width}x{self._shot.height}" if self.template_clip_res.isChecked() else "timeline resolution"
                text += "<br>" + _html(f"{self._shot.source_first}-{self._shot.source_last}, {size}, BMD Film Gen 5. "
                                       f"The cut only, no handles.", GREY, wrap=True)
            self.template_label.setText(text)
            self.template_label.setToolTip(plate.path)
        on = self.render_template.isChecked()
        self.template_format.setEnabled(on)
        self.template_clip_res.setEnabled(on)

    def _pull(self) -> None:
        shot, options = self._shot, self.options()
        if shot is None:
            return
        settings.save_shot_options(options)
        self.close()
        actions.run_pull(shot, options)


def open_pull_dialog() -> None:
    _show("pull", PullDialog)


# #----------------------------------------------------------------------------
# # Send

class SendDialog(_Dialog):
    def __init__(self, info: send_back.SendInfo, parent=None):
        super().__init__("Send comp", parent)
        self._info = info
        self._plan: resolve_bridge.PlacementPlan | None = None
        defaults = settings.load_send_options()

        self.write_label, self.frames_label, self.source_label = _path_label(), _label(), _label()
        self.summary.addRow("Write:", self.write_label)
        self.summary.addRow("Frames:", self.frames_label)
        self.summary.addRow("From:", self.source_label)

        box = QtWidgets.QGroupBox("Placement")
        form = QtWidgets.QFormLayout(box)
        self.placement = QtWidgets.QComboBox()
        for key in resolve_bridge.PLACEMENTS:
            self.placement.addItem(PLACEMENT_LABELS[key], key)
        self.placement.setCurrentIndex(max(0, self.placement.findData(defaults.placement)))
        self.bin = QtWidgets.QLineEdit(defaults.bin)
        self.bin.setToolTip(f"Media pool bin. TCL is evaluated, '/' makes nested bins.\nDefault: {config.DEFAULT_BIN}")
        self.bin_label = _label()
        self.preview_label = _label()
        form.addRow("Place:", self.placement)
        form.addRow("Bin:", self.bin)
        form.addRow("", self.bin_label)
        form.addRow("Preview:", self.preview_label)
        self.layout_.addWidget(box)

        self._buttons("Send")
        self.action_button.clicked.connect(self._send)
        self.placement.activated.connect(self._update_preview)
        # # Debounce typing in the bin field, so we don't ask Resolve on every key.
        self._bin_timer = QtCore.QTimer(self)
        self._bin_timer.setSingleShot(True)
        self._bin_timer.setInterval(400)
        self._bin_timer.timeout.connect(self._update_preview)
        self.bin.textChanged.connect(lambda _: self._bin_timer.start())
        self._update_summary()
        self._update_preview()

    def options(self) -> send_back.SendOptions:
        return send_back.SendOptions(placement=self.placement.currentData(), bin=self.bin.text())

    def refresh(self) -> None:
        try:
            self._info = send_back.gather(self._info.write)
        except (send_back.SendError, ValueError) as e:  # # ValueError: the Write was deleted
            actions.report_error(e if isinstance(e, send_back.SendError) else send_back.SendError("The Write is gone."))
            self.close()
            return
        self._update_summary()
        self._update_preview()

    def _update_summary(self) -> None:
        info = self._info
        path = nuke.filename(info.write)
        self.write_label.setText(_html(info.write.name(), bold=True) + "<br>" + _html(path, GREY))
        self.write_label.setToolTip(path)
        rng = f"{info.first}-{info.last} ({info.frame_count} frames)"
        if info.missing:
            self.frames_label.setText(_html(rng) + "<br>" + _html(
                f"missing {len(info.missing)}: {send_back.format_frames(info.missing)}", RED, wrap=True))
        else:
            self.frames_label.setText(_html(rng) + "  " + _html("all rendered", GREEN))
        s = info.stamp
        self.source_label.setText(_html(f"{s.timeline}, V{s.track} at {s.record_in_tc}") + _html(f"   ({s.project})", GREY))

    def _update_preview(self) -> None:
        options = self.options()
        bin_path, fallback = send_back.evaluate_bin(options.bin)
        self.bin_label.setText(_html(f"→ {bin_path}", ORANGE if fallback else GREY)
                               + (_html(f"   ({fallback})", ORANGE) if fallback else ""))
        try:
            self._plan = send_back.preview(self._info, options)
        except (resolve_bridge.ResolveError, ValueError) as e:
            self._plan = None
            self.preview_label.setText(_html(str(e), RED, wrap=True))
        else:
            p = self._plan
            text = _html(f"→ V{p.track} at {p.record_in_tc}, {p.duration} frames", bold=True)
            if p.placement != options.placement:
                text += _html(f"  ·  fell back to {p.placement.replace('_', ' ')}", ORANGE)
            if p.reuse_media:
                text += _html("  ·  reusing the clip in the bin", GREY)
            # # The bin fallback is already shown under the bin field.
            for w in [w for w in p.warnings if not w.startswith("Bin ")]:
                text += "<br>" + _html(w, ORANGE, wrap=True)
            self.preview_label.setText(text)
        self.action_button.setEnabled(self._plan is not None and not self._info.missing)
        self.fit_height()

    def _send(self) -> None:
        options = self.options()
        write = self._info.write
        settings.save_send_options(options)
        self.close()
        # # run_send gathers again, so frames rendered since the dialog opened count.
        actions.run_send(options, write)


def open_send_dialog() -> None:
    try:
        info = send_back.gather()
    except send_back.SendError as e:
        actions.report_error(e)
        return
    _show("send", SendDialog, info)
