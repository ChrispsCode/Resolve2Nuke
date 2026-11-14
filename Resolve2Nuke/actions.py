"""
Resolve2Nuke.actions
====================

Runs a pull or a send and reports the outcome with Nuke dialogs. Shared by
the dialogs and the no-dialog entry points, so both behave the same.
"""

from __future__ import annotations

import os

import nuke

from Resolve2Nuke import grade_lut
from Resolve2Nuke import logger
from Resolve2Nuke import resolve_bridge
from Resolve2Nuke import send_back
from Resolve2Nuke import shot_build
from Resolve2Nuke import template_plate
from Resolve2Nuke import template_render


def _message(text: str) -> None:
    nuke.message(f"Resolve2Nuke:\n\n{text}")


def report_error(e: Exception) -> None:
    logger.error(str(e))
    _message(str(e))


def run_pull(shot: resolve_bridge.ShotInfo, options: shot_build.ShotOptions | None = None) -> shot_build.BuildResult:
    result = shot_build.build(shot, options)
    if result.unknown_colorspace:
        msg = (f"Unknown colour space: '{shot.resolve_colorspace}'\n\n"
               f"{result.read.name()} was left on its default colorspace, so it probably "
               f"won't match Resolve. Set it by hand, and add the mapping to "
               f"Resolve2Nuke config.RESOLVE_TO_OCIO once it's been tested.")
        logger.warning(msg.replace("\n\n", " "))
        _message(msg)
    logger.info(f"Created {result.read.name()}" + (f" and {result.write.name()}" if result.write else ""))

    if options is not None and options.include_grade:
        result.grade = run_grade(shot, result.kronos or result.read)
    if options is not None and options.render_template:
        result.template = run_template(shot, options, beside=result.read)
    return result


def run_grade(shot: resolve_bridge.ShotInfo, source: nuke.Node) -> nuke.Node | None:
    """Export the clip's grade and add the ResolveGrade reference branch. Errors become a dialog."""
    try:
        path = grade_lut.lut_path()
        if not resolve_bridge.export_grade_lut(shot, path):
            # # Quiet: the option is on by default, and most plates aren't graded yet.
            os.remove(path)
            logger.info(f"{shot.clip_name} has no grade in Resolve, so no grade LUT was added")
            return None
        nuke.Undo.begin("Resolve grade LUT")
        try:
            return grade_lut.create_group(source, path)
        finally:
            nuke.Undo.end()
    except (grade_lut.GradeError, resolve_bridge.ResolveError, OSError) as e:
        report_error(e)
        return None


def run_template(shot: resolve_bridge.ShotInfo, options: shot_build.ShotOptions,
                 beside: nuke.Node | None = None) -> nuke.Node | None:
    """Render the template plate in Resolve, move it into the shot and Read it.
    Its own undo step, after the pull's. Errors become a dialog and return None."""
    task = None
    try:
        plate = template_plate.target(options.template_format)
        try:
            task = nuke.ProgressTask("Resolve2Nuke: template plate")
        except Exception:
            task = None

        def progress(pct: int, msg: str) -> None:
            if task is not None:
                task.setProgress(pct)
                task.setMessage(msg)

        def cancelled() -> bool:
            return bool(task is not None and task.isCancelled())

        rendered = template_render.render_template(shot, options.template_format, options.template_clip_res,
                                                   progress=progress, cancelled=cancelled)
        template_plate.move_into_shot(rendered, plate)
        nuke.Undo.begin("Template plate from Resolve")
        try:
            return template_plate.create_read(plate, shot, beside)
        finally:
            nuke.Undo.end()
    except (template_plate.TemplateError, resolve_bridge.ResolveError, OSError) as e:
        report_error(e)
        return None
    finally:
        # # The progress bar only closes when the task is destroyed.
        del task


def run_send(options: send_back.SendOptions | None = None, write: nuke.Node | None = None,
             ) -> resolve_bridge.PlaceResult | None:
    """Send, then show a summary. Errors become a dialog and return None."""
    try:
        result = send_back.send(write=write, options=options)
    except (send_back.SendError, resolve_bridge.ResolveError) as e:
        report_error(e)
        return None

    summary = (f"Placed {result.clip_name}\non V{result.track} at {result.record_in_tc} "
               f"({result.record_out - result.record_in + 1} frames, {result.placement.replace('_', ' ')}),\n"
               f"bin {result.bin_path}.")
    if result.reused_media:
        summary += "\n\nReused the clip already in that bin."
    if result.warnings:
        for w in result.warnings:
            logger.warning(w)
        summary += "\n\nWarnings:\n- " + "\n- ".join(result.warnings)
    _message(summary)
    return result
