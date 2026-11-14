"""
Resolve2Nuke.template_render
============================

Has Resolve render a "template plate": the clip as it plays in the edit, with
its grade, sizing and retime, encoded as BMD Film Gen 5 like the Writes.

No nuke import. The Nuke side (template_plate.py) picks the destination and
moves the files there afterwards, because Resolve only renders into its
Media Storage locations.

How, without touching the edit:
  1. duplicate the current timeline and delete everything in the copy except
     this clip (the grade, sizing and retime come with it);
  2. optionally render at the clip's resolution, by changing the *project's*
     timeline resolution (custom timeline settings would drop colour management);
  3. save the Deliver page settings as a preset, set the project's output
     colour space (a timeline can't hold its own), and render the clip's
     record range with frame numbers starting at the comp's first frame;
  4. in a finally: restore the output colour space and Deliver settings,
     delete the job and the copy, and go back to the original timeline.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from Resolve2Nuke import config
from Resolve2Nuke import logger
from Resolve2Nuke.resolve_bridge import ResolveError, ShotInfo, connect, find_item_by_id

_PRESET_PREFIX = "R2N_restore_"
_TIMELINE_PREFIX = "R2N template "


_STILL_RENDERING = ("Resolve hasn't stopped rendering, so Resolve2Nuke left its render job and the "
                    "'R2N template' timeline in place. Stop the render in Resolve's Deliver page, then delete "
                    "them and set the project's output colour space back.")


def _wait_until_stopped(project, timeout: float = 30.0) -> bool:
    """True once Resolve isn't rendering (straight away if it wasn't)."""
    end = time.time() + timeout
    while project.IsRenderingInProgress():
        if time.time() > end:
            return False
        time.sleep(0.25)
    return True


@dataclass
class RenderedPlate:
    folder: str          # the staging folder Resolve rendered into
    files: list[str]     # sorted; one .mov, or one EXR per frame
    is_movie: bool


def in_media_storage(resolve, path: str) -> bool:
    path = os.path.realpath(path)
    for volume in resolve.GetMediaStorage().GetMountedVolumeList() or []:
        volume = os.path.realpath(volume)
        if path == volume or path.startswith(volume.rstrip(os.sep) + os.sep):
            return True
    return False


def render_template(shot: ShotInfo, fmt: str = config.DEFAULT_TEMPLATE_FORMAT, clip_res: bool = True,
                    progress: Callable[[int, str], None] | None = None,
                    cancelled: Callable[[], bool] | None = None, resolve=None) -> RenderedPlate:
    """Render the shot's clip as a template plate into a new staging folder."""
    label, render_format, codec, is_movie = config.TEMPLATE_FORMATS[fmt]
    progress = progress or (lambda pct, msg: None)
    cancelled = cancelled or (lambda: False)
    resolve = resolve or connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise ResolveError("Resolve has no project open.")
    # # Read the page before any render call: the render API switches Resolve to
    # # the Deliver page (AddRenderJob does, and so did a call before it). Going
    # # back matters: on Deliver, GetSelectedClips() reports Deliver's own current
    # # clip, and the next pull would take it for the artist's selection.
    saved_page = resolve.GetCurrentPage()
    original = project.GetCurrentTimeline()
    if not original or original.GetName() != shot.timeline_name:
        raise ResolveError(f"Open the timeline '{shot.timeline_name}' in Resolve to render its template plate.")
    item = find_item_by_id(original, shot.item_id)
    if item is None:
        raise ResolveError("The clip isn't in the timeline any more, so its template plate can't be rendered.")

    os.makedirs(config.TEMPLATE_STAGING_DIR, exist_ok=True)
    if not in_media_storage(resolve, config.TEMPLATE_STAGING_DIR):
        raise ResolveError(f"Resolve can only render into its Media Storage locations, and\n"
                           f"{config.TEMPLATE_STAGING_DIR}\nisn't in one. Add it (or a folder above it) in "
                           f"Resolve's Preferences > System > Media Storage.")
    folder = os.path.join(config.TEMPLATE_STAGING_DIR, f"render_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    os.makedirs(folder)

    tag = uuid.uuid4().hex[:8]
    preset = _PRESET_PREFIX + tag
    saved_output = project.GetSetting("colorSpaceOutput")
    saved_mode = project.GetCurrentRenderMode()
    saved_res = None
    preset_saved = project.SaveAsNewRenderPreset(preset)
    duplicate = job = None
    track, record_in = item.GetTrackTypeAndIndex()[1], item.GetStart()
    try:
        progress(2, "Duplicating the timeline")
        duplicate = original.DuplicateTimeline(_TIMELINE_PREFIX + tag)
        if not duplicate:
            raise ResolveError("Resolve couldn't duplicate the timeline.")
        project.SetCurrentTimeline(duplicate)

        # # Keep only this clip (matched by track and position: the copy's IDs are new).
        kept = None
        for t in range(1, duplicate.GetTrackCount("video") + 1):
            for other in duplicate.GetItemListInTrack("video", t) or []:
                if kept is None and t == track and other.GetStart() == record_in:
                    kept = other
                else:
                    duplicate.DeleteClips([other], False)
        if kept is None:
            raise ResolveError("Couldn't find the clip in the duplicated timeline.")

        width = int(duplicate.GetSetting("timelineResolutionWidth"))
        height = int(duplicate.GetSetting("timelineResolutionHeight"))
        if clip_res:
            # # Never turn on the copy's custom settings: that switches it to
            # # unmanaged colour (davinciYRGB), and the plate comes out in the
            # # camera's own encoding (Canon Log 3 read as BMD Film: verified
            # # 2026-11-14). Change the project's resolution instead, which the
            # # copy inherits, and restore it below.
            if duplicate.GetSetting("useCustomSettings") == "1":
                duplicate.SetSetting("timelineResolutionWidth", str(shot.width))
                duplicate.SetSetting("timelineResolutionHeight", str(shot.height))
            else:
                saved_res = (project.GetSetting("timelineResolutionWidth"), project.GetSetting("timelineResolutionHeight"))
                project.SetSetting("timelineResolutionWidth", str(shot.width))
                project.SetSetting("timelineResolutionHeight", str(shot.height))
            width, height = shot.width, shot.height
        if "Managed" not in str(duplicate.GetSetting("colorScienceMode")):
            raise ResolveError(f"The timeline isn't colour managed ({duplicate.GetSetting('colorScienceMode')}), "
                               f"so the plate wouldn't come out as {config.TEMPLATE_RESOLVE_OUTPUT}.")

        if not project.SetSetting("colorSpaceOutput", config.TEMPLATE_RESOLVE_OUTPUT):
            raise ResolveError(f"Couldn't set the output colour space to '{config.TEMPLATE_RESOLVE_OUTPUT}'.")
        if not project.SetCurrentRenderFormatAndCodec(render_format, codec):
            raise ResolveError(f"Resolve can't render {label}.")
        project.SetCurrentRenderMode(1)  # # single clip
        if not project.SetRenderSettings({
            "SelectAllFrames": False, "MarkIn": kept.GetStart(), "MarkOut": kept.GetEnd() - 1,
            "ClipStartFrame": shot.source_first,   # # EXR frame numbers = the comp's frames
            "TargetDir": folder, "CustomName": "plate" if is_movie else "plate.",
            "ExportVideo": True, "ExportAudio": False, "FormatWidth": width, "FormatHeight": height,
        }):
            raise ResolveError("Resolve didn't accept the render settings.")
        job = project.AddRenderJob()
        if not job:
            raise ResolveError("Resolve couldn't add the render job (is a dialog open in Resolve?).")

        project.StartRendering([job], False)
        while project.IsRenderingInProgress():
            if cancelled():
                project.StopRendering()
                # # StopRendering() only asks. Touching the job or the timeline before
                # # Resolve has really stopped wedged Resolve (2026-11-14).
                if not _wait_until_stopped(project):
                    raise ResolveError(_STILL_RENDERING)
                raise ResolveError("Template render cancelled.")
            status = project.GetRenderJobStatus(job) or {}
            progress(5 + int(0.9 * status.get("CompletionPercentage", 0)), f"Rendering {label}")
            time.sleep(0.25)
        status = project.GetRenderJobStatus(job) or {}
        if status.get("JobStatus") != "Complete":
            raise ResolveError(f"Resolve's render didn't complete: {status.get('JobStatus')} {status.get('Error', '')}".strip())
    except BaseException:
        # # Don't leave a partial render behind in staging.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        # # Never clean up under a running render: deleting the timeline being
        # # rendered hangs Resolve. If it won't stop, leave the job and the copy.
        if _wait_until_stopped(project):
            project.SetSetting("colorSpaceOutput", saved_output)
            project.SetCurrentRenderMode(saved_mode)
            if saved_res:
                project.SetSetting("timelineResolutionWidth", saved_res[0])
                project.SetSetting("timelineResolutionHeight", saved_res[1])
            if preset_saved:
                project.LoadRenderPreset(preset)
                project.DeleteRenderPreset(preset)
            if job:
                project.DeleteRenderJob(job)
            project.SetCurrentTimeline(original)
            if duplicate:
                project.GetMediaPool().DeleteTimelines([duplicate])
            if saved_page and resolve.GetCurrentPage() != saved_page:
                resolve.OpenPage(saved_page)
        else:
            logger.error(_STILL_RENDERING)

    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if not f.startswith("."))
    if not files:
        raise ResolveError(f"Resolve's render finished but wrote nothing to {folder}.")
    logger.info(f"Rendered {len(files)} file(s) to {folder}")
    progress(95, "Moving the plate into the shot")
    return RenderedPlate(folder=folder, files=files, is_movie=is_movie)
