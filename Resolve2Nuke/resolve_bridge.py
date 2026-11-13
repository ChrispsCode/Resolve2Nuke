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


def item_source_first(item) -> int:
    """The Nuke frame number of the item's first frame in the timeline.

    Nuke numbers movie files (BRAW, mov) from 1, and sequences by their file
    numbers. Resolve's "Start" is 0 for movies and the first file number for
    sequences. GetSourceStartFrame() is off by one for BRAW, so use the left
    offset (verified against the viewer's source timecode).
    """
    props = item.GetMediaPoolItem().GetClipProperty()
    clip_start = int(props.get("Start") or 0)
    is_sequence = bool(_SEQUENCE_RE.search(props.get("File Path") or ""))
    nuke_first_of_clip = clip_start if is_sequence else clip_start + 1
    return nuke_first_of_clip + item.GetLeftOffset()


def _shot_from_item(project, timeline, item) -> ShotInfo:
    mpi = item.GetMediaPoolItem()
    props = mpi.GetClipProperty()
    file_path, is_sequence = _nuke_path(props["File Path"])

    source_first = item_source_first(item)
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


# #----------------------------------------------------------------------------
# # Sending a comp back

PLACEMENTS = ("follow_clip", "stored", "playhead")


@dataclass
class CompPlacement:
    """What to place, and where it came from (the stamp, as plain values)."""
    sequence_path: str       # Nuke-style, e.g. /x/c_shot_comp_v01.%04d.exr
    first: int               # first rendered frame (= source frame number)
    last: int
    project: str
    timeline: str
    item_id: str
    track: int
    record_in: int
    source_first: int


@dataclass
class PlacementPlan:
    """Where a comp would land. Working it out changes nothing in Resolve."""
    placement: str           # the mode actually used (follow_clip can fall back to stored)
    track: int               # may be above the timeline's current track count
    record_in: int
    record_out: int
    record_in_tc: str
    bin_path: str
    reuse_media: bool        # the sequence is already in that bin
    warnings: list

    @property
    def duration(self) -> int:
        return self.record_out - self.record_in + 1


@dataclass
class PlaceResult:
    placement: str
    track: int
    record_in: int
    record_out: int
    record_in_tc: str
    clip_name: str
    bin_path: str
    reused_media: bool
    warnings: list


def find_item_by_id(timeline, item_id: str):
    """The video item with this unique ID, wherever it is now, or None."""
    for track in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            if item.GetUniqueId() == item_id:
                return item
    return None


def _anchor(timeline, comp: CompPlacement, placement: str, warnings: list) -> tuple[str, int, int]:
    """(mode used, record frame of comp.first, track the comp goes above)."""
    if placement == "follow_clip":
        item = find_item_by_id(timeline, comp.item_id)
        if item is not None:
            # # Align by source frame, so a moved or trimmed clip still lines up.
            record = item.GetStart() + (comp.first - item_source_first(item))
            return "follow_clip", record, item.GetTrackTypeAndIndex()[1]
        warnings.append("The original clip isn't in the timeline any more (deleted or replaced), "
                        "so the comp goes at the position stored when the shot was pulled.")
        placement = "stored"

    if placement == "stored":
        return "stored", comp.record_in + (comp.first - comp.source_first), comp.track

    if placement == "playhead":
        tc = timeline.GetCurrentTimecode()
        if ";" in tc or timeline.GetSetting("timelineDropFrameTimecode") == "1":
            raise ResolveError("Placing at the playhead isn't supported on drop-frame timelines yet.")
        record = _timecode_to_frame(tc, float(timeline.GetSetting("timelineFrameRate")))
        under = _playhead_video_item(timeline)
        return "playhead", record, under.GetTrackTypeAndIndex()[1] if under else 0

    raise ValueError(f"Unknown placement '{placement}', expected one of {PLACEMENTS}")


def _track_is_free(timeline, track: int, record_in: int, record_out: int) -> bool:
    if track > timeline.GetTrackCount("video"):
        return True
    for item in timeline.GetItemListInTrack("video", track) or []:
        if item.GetStart() <= record_out and item.GetEnd() - 1 >= record_in:
            return False
    return True


def _free_track_above(timeline, base_track: int, record_in: int, record_out: int) -> int:
    """The first track above base_track that's empty for the range (may not exist yet)."""
    track = base_track + 1
    while not _track_is_free(timeline, track, record_in, record_out):
        track += 1
    return track


# #----------------------------------------------------------------------------
# # Bins

def split_bin_path(bin_path: str) -> list[str]:
    """'VFX//shot/' -> ['VFX', 'shot']"""
    return [part.strip() for part in bin_path.replace("\\", "/").split("/") if part.strip()]


def _find_bin(media_pool, bin_path: str, create: bool):
    """The bin at bin_path under the media pool root, created level by level when asked."""
    folder = media_pool.GetRootFolder()
    for name in split_bin_path(bin_path):
        child = next((f for f in folder.GetSubFolderList() or [] if f.GetName() == name), None)
        if child is None:
            if not create:
                return None
            child = media_pool.AddSubFolder(folder, name)
            if not child:
                raise ResolveError(f"Couldn't create the bin '{name}' in '{bin_path}'.")
        folder = child
    return folder


def _resolve_sequence_name(sequence_path: str, first: int, last: int) -> str:
    """/x/name.%04d.exr -> /x/name.[0107-0145].exr, the way Resolve shows a sequence's File Path."""
    match = re.search(r"%0(\d+)d", sequence_path)
    pad = int(match.group(1)) if match else 0
    return sequence_path[:match.start()] + f"[{first:0{pad}d}-{last:0{pad}d}]" + sequence_path[match.end():]


def _existing_clip(folder, comp: CompPlacement):
    if folder is None:
        return None
    resolve_path = _resolve_sequence_name(comp.sequence_path, comp.first, comp.last)
    return next((c for c in folder.GetClipList() or [] if c.GetClipProperty("File Path") == resolve_path), None)


def _import_sequence(media_pool, folder, comp: CompPlacement):
    previous = media_pool.GetCurrentFolder()
    media_pool.SetCurrentFolder(folder)
    try:
        items = media_pool.ImportMedia([{"FilePath": comp.sequence_path, "StartIndex": comp.first, "EndIndex": comp.last}])
    finally:
        if previous:
            media_pool.SetCurrentFolder(previous)
    if not items:
        raise ResolveError(f"Resolve couldn't import {comp.sequence_path} ({comp.first}-{comp.last}).")
    return items[0]


# #----------------------------------------------------------------------------
# # Plan and place

def _current(resolve):
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise ResolveError("Resolve has no project open.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveError(f"Project '{project.GetName()}' has no timeline open.")
    return project, timeline


def plan_placement(comp: CompPlacement, placement: str = "follow_clip", bin_path: str = "VFX",
                   resolve=None) -> PlacementPlan:
    """Where the comp would land. Read-only: nothing in Resolve changes."""
    resolve = resolve or connect()
    project, timeline = _current(resolve)
    if not split_bin_path(bin_path):
        raise ResolveError("The bin name is empty.")

    warnings = []
    if project.GetName() != comp.project:
        warnings.append(f"Resolve's project is '{project.GetName()}', but the shot came from '{comp.project}'.")
    if timeline.GetName() != comp.timeline:
        warnings.append(f"Resolve's timeline is '{timeline.GetName()}', but the shot came from '{comp.timeline}'.")

    used, record_in, base_track = _anchor(timeline, comp, placement, warnings)
    record_out = record_in + (comp.last - comp.first)
    track = _free_track_above(timeline, base_track, record_in, record_out)
    folder = _find_bin(project.GetMediaPool(), bin_path, create=False)
    fps = float(timeline.GetSetting("timelineFrameRate"))
    return PlacementPlan(placement=used, track=track, record_in=record_in, record_out=record_out,
                         record_in_tc=_frame_to_timecode(record_in, fps), bin_path="/".join(split_bin_path(bin_path)),
                         reuse_media=_existing_clip(folder, comp) is not None, warnings=warnings)


def place_comp(comp: CompPlacement, placement: str = "follow_clip", bin_path: str = "VFX",
               resolve=None) -> PlaceResult:
    """Import a rendered comp and put it on the timeline above its plate."""
    resolve = resolve or connect()
    project, timeline = _current(resolve)
    plan = plan_placement(comp, placement, bin_path, resolve)

    while timeline.GetTrackCount("video") < plan.track:
        if not timeline.AddTrack("video"):
            raise ResolveError("Couldn't add a video track to the timeline.")

    media_pool = project.GetMediaPool()
    folder = _find_bin(media_pool, plan.bin_path, create=True)
    clip = _existing_clip(folder, comp)
    reused = clip is not None
    if clip is None:
        clip = _import_sequence(media_pool, folder, comp)

    # # For sequences, start/endFrame are file frame numbers and endFrame is exclusive.
    placed = media_pool.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": comp.first, "endFrame": comp.last + 1,
                                           "trackIndex": plan.track, "recordFrame": plan.record_in}])
    if not placed:
        raise ResolveError(f"Resolve couldn't place the comp on V{plan.track} at frame {plan.record_in}.")
    item = placed[0]
    warnings = list(plan.warnings)
    if (item.GetStart(), item.GetDuration()) != (plan.record_in, plan.duration):
        warnings.append(f"Resolve placed the comp at {item.GetStart()} for {item.GetDuration()} frames, "
                        f"expected {plan.record_in} for {plan.duration}.")

    fps = float(timeline.GetSetting("timelineFrameRate"))
    result = PlaceResult(placement=plan.placement, track=plan.track, record_in=item.GetStart(),
                         record_out=item.GetEnd() - 1, record_in_tc=_frame_to_timecode(item.GetStart(), fps),
                         clip_name=clip.GetName(), bin_path=plan.bin_path, reused_media=reused, warnings=warnings)
    logger.info(f"Placed {result.clip_name} on V{result.track} at {result.record_in_tc} ({result.placement}), "
                f"bin {result.bin_path}")
    return result
