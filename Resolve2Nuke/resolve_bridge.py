"""
Resolve2Nuke.resolve_bridge
===========================

Reads the shot to pull from the running DaVinci Resolve and returns it as a
plain ShotInfo. Deliberately has no `nuke` import, so it can be tested from
any Python that can reach Resolve.

Frame numbers in ShotInfo are *Nuke* frame numbers of the source file, not
1001-based. The comp works on the plate's own numbers so handles and
extensions are simple arithmetic.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

from Resolve2Nuke import config
from Resolve2Nuke import logger


class ResolveError(RuntimeError):
    """A problem talking to Resolve, with a message meant for the artist."""


# # Resolve shows image sequences as "name.[0107-0145].exr"
_SEQUENCE_RE = re.compile(r"\[(\d+)-(\d+)\]")


@dataclass
class ShotInfo:
    # # Source file
    file_path: str          # Nuke-ready, sequences as %0Nd
    clip_name: str
    is_sequence: bool
    width: int
    height: int
    clip_fps: float
    resolve_colorspace: str  # Resolve's input colour space for the clip (resolved from "Project")

    # # The cut, in Nuke frame numbers of the source file
    source_first: int
    source_last: int

    # # Where it sits in Resolve
    project_name: str
    timeline_name: str
    timeline_fps: float
    track_index: int
    record_in: int           # first timeline frame
    record_out: int          # last timeline frame (inclusive)
    record_in_tc: str
    item_id: str

    @property
    def duration(self) -> int:
        return self.source_last - self.source_first + 1


# #----------------------------------------------------------------------------
# # Connection

def connect():
    """Return Resolve's scripting app, or raise ResolveError explaining why not.

    The in-process import works in Nuke 17.1. In 15.2 Resolve's fusionscript
    fails on a libc++ symbol clash, which is why the tool is gated to 17+.
    """
    os.environ.setdefault("RESOLVE_SCRIPT_API", config.RESOLVE_SCRIPT_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", config.RESOLVE_SCRIPT_LIB)
    modules_dir = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if modules_dir not in sys.path:
        sys.path.append(modules_dir)

    try:
        import DaVinciResolveScript as dvr
    except Exception as e:
        raise ResolveError(f"Couldn't load Resolve's scripting module ({e}).\n"
                           f"Is DaVinci Resolve Studio installed at the default location?") from e

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError("Couldn't connect to Resolve.\n"
                           "Make sure it's running, and that Preferences > System > General > "
                           "'External scripting using' is set to Local.")
    return resolve


# #----------------------------------------------------------------------------
# # Picking the clip

def _timecode_to_frame(tc: str, fps: float) -> int:
    """Non-drop-frame timecode to an absolute frame number."""
    h, m, s, f = (int(x) for x in re.split(r"[:;]", tc))
    rate = round(fps)
    return ((h * 60 + m) * 60 + s) * rate + f


def _is_pullable(item) -> bool:
    """Only clips with source media (not titles, generators or compound clips without a file)."""
    mpi = item.GetMediaPoolItem()
    return bool(mpi and mpi.GetClipProperty("File Path"))


def _selected_video_item(timeline):
    """The selected video clip, topmost first if several are selected."""
    selected = [i for i in (timeline.GetSelectedClips() or [])
                if i.GetTrackTypeAndIndex()[0] == "video" and _is_pullable(i)]
    if not selected:
        return None
    selected.sort(key=lambda i: i.GetTrackTypeAndIndex()[1], reverse=True)
    if len(selected) > 1:
        logger.warning(f"{len(selected)} clips selected, using the topmost: {selected[0].GetName()}")
    return selected[0]


def _playhead_video_item(timeline):
    """The topmost enabled clip under the playhead on an enabled video track."""
    tc = timeline.GetCurrentTimecode()
    drop_frame = timeline.GetSetting("timelineDropFrameTimecode") == "1" or ";" in tc
    if drop_frame:
        # # Rare for us. Resolve's own pick is good enough here.
        item = timeline.GetCurrentVideoItem()
        return item if item and _is_pullable(item) else None

    frame = _timecode_to_frame(tc, float(timeline.GetSetting("timelineFrameRate")))
    for track in range(timeline.GetTrackCount("video"), 0, -1):
        if not timeline.GetIsTrackEnabled("video", track):
            continue
        for item in timeline.GetItemListInTrack("video", track) or []:
            if item.GetStart() <= frame < item.GetEnd() and item.GetClipEnabled() and _is_pullable(item):
                return item
    return None


# #----------------------------------------------------------------------------
# # Building ShotInfo

def _nuke_path(file_path: str) -> tuple[str, bool]:
    """Resolve's 'name.[0107-0145].exr' -> 'name.%04d.exr'."""
    match = _SEQUENCE_RE.search(file_path)
    if not match:
        return file_path, False
    padding = len(match.group(1))
    return file_path[:match.start()] + f"%0{padding}d" + file_path[match.end():], True


def _shot_from_item(project, timeline, item) -> ShotInfo:
    mpi = item.GetMediaPoolItem()
    props = mpi.GetClipProperty()
    file_path, is_sequence = _nuke_path(props["File Path"])

    # # Nuke numbers movie files (BRAW, mov) from 1, and sequences by their
    # # file numbers. Resolve's "Start" is 0 for movies and the first file
    # # number for sequences. GetSourceStartFrame() is off by one for BRAW, so
    # # use the left offset (verified against the viewer's source timecode).
    clip_start = int(props.get("Start") or 0)
    nuke_first_of_clip = clip_start if is_sequence else clip_start + 1
    source_first = nuke_first_of_clip + item.GetLeftOffset()
    source_last = source_first + item.GetDuration() - 1

    colorspace = props.get("Input Color Space") or "Project"
    if colorspace.startswith("Project"):
        colorspace = project.GetSetting("colorSpaceInput")

    width, height = (int(v) for v in props["Resolution"].lower().split("x"))
    track_index = item.GetTrackTypeAndIndex()[1]

    record_in = item.GetStart()
    return ShotInfo(
        file_path=file_path,
        clip_name=props.get("File Name") or item.GetName(),
        is_sequence=is_sequence,
        width=width,
        height=height,
        clip_fps=float(props.get("FPS") or 0),
        resolve_colorspace=colorspace,
        source_first=source_first,
        source_last=source_last,
        project_name=project.GetName(),
        timeline_name=timeline.GetName(),
        timeline_fps=float(timeline.GetSetting("timelineFrameRate")),
        track_index=track_index,
        record_in=record_in,
        record_out=item.GetEnd() - 1,
        record_in_tc=_frame_to_timecode(record_in, float(timeline.GetSetting("timelineFrameRate"))),
        item_id=item.GetUniqueId(),
    )


def _frame_to_timecode(frame: int, fps: float) -> str:
    rate = round(fps)
    f = frame % rate
    s = frame // rate
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def get_shot(resolve=None) -> ShotInfo:
    """The selected video clip in Resolve, or else the topmost one under the playhead."""
    resolve = resolve or connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise ResolveError("Resolve has no project open.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveError(f"Project '{project.GetName()}' has no timeline open.")

    item = _selected_video_item(timeline)
    source = "selection"
    if item is None:
        item = _playhead_video_item(timeline)
        source = "playhead"
    if item is None:
        raise ResolveError(f"No clip selected, and no clip with source media under the playhead "
                           f"({timeline.GetCurrentTimecode()}) on an enabled video track.")

    shot = _shot_from_item(project, timeline, item)
    logger.info(f"Pulled from {source}: {shot.clip_name} V{shot.track_index}, "
                f"source {shot.source_first}-{shot.source_last} ({shot.duration} frames)")
    return shot
