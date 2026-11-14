"""
Resolve2Nuke.fcpxml
===================

Reads a clip's retime curve from Resolve's FCPXML export. The scripting API
only gives a constant speed (GetSpeed() reports an average for ramps), but
FCPXML carries the full <timeMap>: clip time -> source time, with each key's
interpolation.

No nuke or Qt imports, so it can be tested anywhere.

FCPXML layout (Resolve 21.1, verified 2026-11-14):
  - V1 clips are children of <spine>, their offset is absolute timeline time.
  - Clips on V2+ are nested inside a V1 clip with lane="1", "2"...; their offset
    is in the parent's local time: absolute = parent offset + (offset - parent start).
  - Retimed clips are <clip> (with a <video ref=...> child) or <asset-clip ref=...>,
    holding <timeMap><timept time= value= interp= [inTime= outTime=]/></timeMap>.
    time is the clip's local time, value the source time, both in the source's
    own seconds (the asset's start = the source's start timecode).
  - interp describes the key itself: "linear" is a sharp corner, "smooth2" an
    eased corner whose ease starts inTime before the key and ends outTime after.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction


def _seconds(text: str | None) -> Fraction:
    """'86415/2s' -> Fraction(86415, 2). FCPXML times are rational seconds."""
    if not text:
        return Fraction(0)
    return Fraction(text.rstrip("s"))


@dataclass
class TimePoint:
    time: float          # clip local time (s)
    value: float         # source time (s)
    interp: str
    in_time: float = 0.0
    out_time: float = 0.0


@dataclass
class RetimeCurve:
    points: list[TimePoint]
    clip_start: float     # clip local time at the clip's first timeline frame
    asset_start: float    # source time of the source's first frame

    def value(self, t: float) -> float:
        """Source time at clip time t."""
        pts = self.points
        if t <= pts[0].time:
            return self._extrapolate(pts[0], pts[1], t)
        if t >= pts[-1].time:
            return self._extrapolate(pts[-2], pts[-1], t)

        # # Eased corners first: inside a key's ease window, follow a quadratic
        # # Bezier from the incoming line to the outgoing line, with the key as
        # # its control point.
        for i in range(1, len(pts) - 1):
            key = pts[i]
            if key.interp == "linear" or not (key.in_time or key.out_time):
                continue
            prev, nxt = pts[i - 1], pts[i + 1]
            # # Keep the window inside its neighbouring segments.
            t0 = key.time - min(key.in_time, (key.time - prev.time) / 2)
            t2 = key.time + min(key.out_time, (nxt.time - key.time) / 2)
            if t0 <= t <= t2 and t2 > t0:
                v0 = self._line(prev, key, t0)
                v2 = self._line(key, nxt, t2)
                s = self._bezier_param(t0, key.time, t2, t)
                return (1 - s) ** 2 * v0 + 2 * s * (1 - s) * key.value + s ** 2 * v2

        for a, b in zip(pts, pts[1:]):
            if a.time <= t <= b.time:
                return self._line(a, b, t)
        return pts[-1].value

    @staticmethod
    def _line(a: TimePoint, b: TimePoint, t: float) -> float:
        if b.time == a.time:
            return a.value
        return a.value + (b.value - a.value) * (t - a.time) / (b.time - a.time)

    def _extrapolate(self, a: TimePoint, b: TimePoint, t: float) -> float:
        return self._line(a, b, t)

    @staticmethod
    def _bezier_param(t0: float, t1: float, t2: float, t: float) -> float:
        """Solve (1-s)^2 t0 + 2s(1-s) t1 + s^2 t2 = t for s in [0, 1]."""
        a = t0 - 2 * t1 + t2
        b = 2 * (t1 - t0)
        c = t0 - t
        if abs(a) < 1e-12:
            return min(1.0, max(0.0, -c / b)) if b else 0.0
        disc = max(0.0, b * b - 4 * a * c)
        for s in ((-b + math.sqrt(disc)) / (2 * a), (-b - math.sqrt(disc)) / (2 * a)):
            if -1e-9 <= s <= 1 + 1e-9:
                return min(1.0, max(0.0, s))
        return 0.0

    def source_frames(self, duration: int, timeline_fps: float, source_fps: float) -> list[float]:
        """0-based source frame (fractional) for each of the clip's timeline frames."""
        return [(self.value(self.clip_start + k / timeline_fps) - self.asset_start) * source_fps
                for k in range(duration)]


# #----------------------------------------------------------------------------
# # Export and lookup

def export(timeline, resolve) -> str:
    """Export the timeline to a temp folder. Returns the folder (caller removes it)."""
    folder = tempfile.mkdtemp(prefix="r2n_fcpxml_")
    path = os.path.join(folder, "timeline.fcpxml")
    if not timeline.Export(path, resolve.EXPORT_FCPXML_1_10, resolve.EXPORT_NONE):
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError("Resolve couldn't export the timeline as FCPXML.")
    return folder


def _xml_file(folder: str) -> str:
    """Resolve writes .fcpxml as a bundle folder with Info.fcpxml inside (or a plain file)."""
    path = os.path.join(folder, "timeline.fcpxml")
    bundle = os.path.join(path, "Info.fcpxml")
    return bundle if os.path.isdir(path) else path


def _walk_clips(element, parent_abs: Fraction | None, parent_start: Fraction, lane: int):
    """Yield (clip element, absolute timeline seconds, track index) for video clips."""
    for child in element:
        if child.tag not in ("asset-clip", "clip", "ref-clip", "sync-clip", "gap", "mc-clip"):
            continue
        child_lane = int(child.get("lane", "0")) if parent_abs is not None else 0
        if child_lane < 0:
            continue  # # audio
        offset = _seconds(child.get("offset"))
        absolute = offset if parent_abs is None else parent_abs + (offset - parent_start)
        track = (lane if parent_abs is None else child_lane) + 1
        if child.tag != "gap":
            yield child, absolute, track
        # # Connected clips (V2+) hang off this clip, in its local time.
        yield from _walk_clips(child, absolute, _seconds(child.get("start")), 0)


def find_curve(folder: str, record_in: int, track: int, timeline_fps: float) -> RetimeCurve | None:
    """The retime curve of the clip at record_in on track, or None if it isn't retimed."""
    root = ET.parse(_xml_file(folder)).getroot()
    assets = {a.get("id"): a for a in root.iter("asset")}
    spine = root.find(".//spine")
    if spine is None:
        return None
    target = Fraction(record_in) / Fraction(timeline_fps).limit_denominator(1001)
    for clip, absolute, clip_track in _walk_clips(spine, None, Fraction(0), 0):
        if clip_track != track or abs(float(absolute - target)) > 0.5 / timeline_fps:
            continue
        time_map = clip.find("timeMap")
        if time_map is None:
            return None
        ref = clip.get("ref") or (clip.find("video").get("ref") if clip.find("video") is not None else None)
        asset = assets.get(ref)
        points = [TimePoint(time=float(_seconds(p.get("time"))), value=float(_seconds(p.get("value"))),
                            interp=p.get("interp", "linear"), in_time=float(_seconds(p.get("inTime"))),
                            out_time=float(_seconds(p.get("outTime"))))
                  for p in time_map.findall("timept")]
        if len(points) < 2:
            return None
        return RetimeCurve(points=points, clip_start=float(_seconds(clip.get("start"))),
                           asset_start=float(_seconds(asset.get("start"))) if asset is not None else points[0].time)
    return None


def retime_curve(timeline, resolve, record_in: int, track: int) -> RetimeCurve | None:
    """Export, look up, clean up."""
    folder = export(timeline, resolve)
    try:
        return find_curve(folder, record_in, track, float(timeline.GetSetting("timelineFrameRate")))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
