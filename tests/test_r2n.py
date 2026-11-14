"""Resolve2Nuke headless tests. Needs Resolve open on the VFX Test project (one BRAW on V1 at 01:00:00:00).

/opt/Nuke17.1v1/Nuke17.1 -ti tests/test_r2n.py   (renders go to $R2N_TEST_DIR, default /tmp/r2n_test)
"""
import dataclasses
import os
import shutil
import traceback

SCRATCH = os.environ.get("R2N_TEST_DIR", "/tmp/r2n_test")
os.environ["SHOT_ROOT"] = f"{SCRATCH}/shotroot"
# # Start clean: leftover renders would make the "missing frames" send succeed and shift every placement.
import shutil as _shutil
_shutil.rmtree(os.environ["SHOT_ROOT"], ignore_errors=True)
os.environ["SHOT_NAME"] = "test_tree_sc010_sh010"

import nuke

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


import Resolve2Nuke
from Resolve2Nuke import config, resolve_bridge, send_back, settings, shot_build, stamp
from Resolve2Nuke.qt import QtCore

print("Resolve2Nuke", Resolve2Nuke.__version__)
messages = []
nuke.message = lambda m: messages.append(m)
config.SETTINGS_ORG = "Resolve2NukeTest"

resolve = resolve_bridge.connect()
project = resolve.GetProjectManager().GetCurrentProject()
mp = project.GetMediaPool()
user_timeline = project.GetCurrentTimeline()
TEST_TIMELINE = "R2N Test"
TEST_BINS = ["VFX/test_tree_sc010_sh010", "Comps/A", "Comps"]  # # only these are ever deleted


def find_bin(path):
    folder = mp.GetRootFolder()
    for name in path.split("/"):
        folder = next((f for f in folder.GetSubFolderList() or [] if f.GetName() == name), None)
        if folder is None:
            return None
    return folder


def fcpxml_start(item):
    """The source's start in seconds, from its start timecode (24 fps BRAW)."""
    h, m, sec, f = (int(x) for x in item.GetMediaPoolItem().GetClipProperty("Start TC").split(":"))
    return h * 3600 + m * 60 + sec + f / 24.0


def make_test_timeline():
    """A fresh timeline with the baseline cut: the BRAW, source 107-145, at 01:00:00:00 on V1."""
    braw = []

    def walk(folder):
        braw.extend(c for c in folder.GetClipList() or [] if c.GetName() == "A001_08241708_C025.braw")
        for sub in folder.GetSubFolderList() or []:
            walk(sub)
    walk(mp.GetRootFolder())
    for i in range(project.GetTimelineCount(), 0, -1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == TEST_TIMELINE:
            mp.DeleteTimelines([t])
    tl = mp.CreateEmptyTimeline(TEST_TIMELINE)
    project.SetCurrentTimeline(tl)
    # # Movie clips: startFrame is 0-based, endFrame exclusive (106, 145 -> left offset 106, 39 frames)
    mp.AppendToTimeline([{"mediaPoolItem": braw[0], "startFrame": 106, "endFrame": 145, "trackIndex": 1, "recordFrame": tl.GetStartFrame()}])
    # # The same cut at 50%, at 01:00:04:04 (AppendToTimeline always targets the *current* timeline)
    slow = mp.AppendToTimeline([{"mediaPoolItem": braw[0], "startFrame": 106, "endFrame": 145, "trackIndex": 1, "recordFrame": 86500}])
    slow[0].SetSpeed({"Percentage": 50.0})
    return tl


timeline = make_test_timeline()


def cleanup_resolve():
    """Empty the test timeline above V1 and delete only the bins the tests make."""
    for t in range(timeline.GetTrackCount("video"), 1, -1):
        items = timeline.GetItemListInTrack("video", t) or []
        if items:
            timeline.DeleteClips(items, False)
        timeline.DeleteTrack("video", t)
    for path in TEST_BINS:
        folder = find_bin(path)
        if folder is not None:
            mp.DeleteFolders([folder])
    vfx = find_bin("VFX")
    if vfx is not None and not vfx.GetClipList() and not vfx.GetSubFolderList():
        mp.DeleteFolders([vfx])


def snapshot():
    tracks = timeline.GetTrackCount("video")
    items = [(t, i.GetUniqueId()) for t in range(1, tracks + 1) for i in timeline.GetItemListInTrack("video", t) or []]
    bins = sorted(f.GetName() for f in mp.GetRootFolder().GetSubFolderList() or [])
    return tracks, items, bins


def v_items(track):
    return [(i.GetName(), i.GetStart(), i.GetEnd() - 1, i.GetLeftOffset()) for i in timeline.GetItemListInTrack("video", track) or []]


cleanup_resolve()
timeline.SetCurrentTimecode("01:00:00:00")

try:
    # #================ PULL
    shot = resolve_bridge.get_shot()
    check("bridge: source 107-145", (shot.source_first, shot.source_last) == (107, 145), (shot.source_first, shot.source_last))
    check("bridge: test timeline", shot.timeline_name == TEST_TIMELINE)
    check("bridge: record 86400-86438, TC", (shot.record_in, shot.record_out, shot.record_in_tc) == (86400, 86438, "01:00:00:00"))
    check("bridge: colour/res/fps/track", (shot.resolve_colorspace, shot.width, shot.height, shot.clip_fps, shot.timeline_fps, shot.track_index)
          == ("Blackmagic Design Film Gen 5", 4096, 2160, 24.0, 24.0, 1))

    nuke.scriptClear()
    while nuke.Undo.disabled():
        nuke.Undo.enable()
    res = Resolve2Nuke.pull_shot()  # # headless: runs directly, no dialog
    root, r, w = nuke.root(), res.read, res.write
    check("pull: root OCIO/fps/format/range", (root["OCIO_config"].value(), root["fps"].value(), root.format().width(),
          root["first_frame"].value(), root["last_frame"].value()) == (config.OCIO_CONFIG, 24, 4096, 107, 145))
    check("pull: read range/colour/decode", (r["first"].value(), r["last"].value(), r["colorspace"].value(), r["decode_using"].getValue())
          == (1, 282, "BMDFilm WideGamut Gen5", 0))
    check("pull: read timecode at 107", r.metadata("input/timecode", 107) == "12:00:07:12")
    check("pull: write knobs", (w.input(0) is r, w["colorspace"].value(), w["datatype"].value(), w["create_directories"].value())
          == (True, "BMDFilm WideGamut Gen5", "16 bit half", True))
    check("pull: read and write stamped alike", stamp.read_stamp(w) == stamp.read_stamp(r) and stamp.read_stamp(r).item_id == shot.item_id)
    nuke.undo()
    check("pull: one undo step", not nuke.allNodes())

    nuke.scriptClear()
    before = (root["OCIO_config"].value(), root["fps"].value(), root.format().width(), root["first_frame"].value(), root["last_frame"].value())
    res = shot_build.build(shot, shot_build.ShotOptions(False, False, "timeline", False, False, 0, False))
    after = (root["OCIO_config"].value(), root["fps"].value(), root.format().width(), root["first_frame"].value(), root["last_frame"].value())
    check("pull options off: root untouched, no write", before == after and res.write is None, (before, after))

    nuke.scriptClear()
    shot_build.build(dataclasses.replace(shot, clip_fps=25.0), shot_build.ShotOptions(handles=8, fps_source="clip"))
    check("pull: handles 8, clip fps 25", (root["first_frame"].value(), root["last_frame"].value(), root["fps"].value()) == (99, 153, 25))

    nuke.scriptClear()
    messages.clear()
    from Resolve2Nuke import actions
    res = actions.run_pull(dataclasses.replace(shot, resolve_colorspace="Sony S-Log3"))
    check("pull: unknown colour space dialog", res.unknown_colorspace and len(messages) == 1 and "Sony S-Log3" in messages[0])

    # #================ SEND
    nuke.scriptClear()
    res = Resolve2Nuke.pull_shot()
    w, r = res.write, res.read
    try:
        send_back.send(w)
        check("send: missing frames raises", False)
    except send_back.SendError as e:
        check("send: missing frames raises", "missing 39 of 39" in str(e) and "107-145" in str(e), str(e))
    nuke.execute(w, 107, 145)
    info = send_back.gather(w)
    check("render: all frames", not info.missing and info.frame_count == 39)

    # # preview is read-only and matches the real placement
    before = snapshot()
    plans = {m: send_back.preview(info, send_back.SendOptions(m)) for m in resolve_bridge.PLACEMENTS}
    check("preview: Resolve untouched", snapshot() == before, (before, snapshot()))
    check("preview: nested bin path", plans["stored"].bin_path == "VFX/test_tree_sc010_sh010", plans["stored"].bin_path)
    check("preview: V2 at 01:00:00:00", (plans["stored"].track, plans["stored"].record_in_tc) == (2, "01:00:00:00"))

    pr = send_back.send(w, send_back.SendOptions("stored"))
    check("stored: matches preview", (pr.track, pr.record_in, pr.record_out) == (2, 86400, 86438), (pr.track, pr.record_in, pr.record_out))
    check("stored: source 107 at left offset 0", v_items(2) and v_items(2)[0][3] == 0, v_items(2))
    shot_bin = find_bin("VFX/test_tree_sc010_sh010")
    check("stored: clip in nested bin", shot_bin is not None and len(shot_bin.GetClipList()) == 1)
    p2 = send_back.preview(send_back.gather(w), send_back.SendOptions("stored"))
    check("preview after send: V3, reuse", (p2.track, p2.reuse_media) == (3, True), (p2.track, p2.reuse_media))
    pr2 = send_back.send(w, send_back.SendOptions("stored"))
    check("re-send: V3, reused media", (pr2.track, pr2.reused_media) == (3, True))
    pr3 = send_back.send(w, send_back.SendOptions("stored", bin="Comps/A"))
    check("other bin: fresh import there", (pr3.bin_path, pr3.reused_media, pr3.track) == ("Comps/A", False, 4), (pr3.bin_path, pr3.reused_media, pr3.track))
    cleanup_resolve()

    pr = send_back.send(w)
    check("follow_clip: default, V2 86400", (pr.placement, pr.track, pr.record_in) == ("follow_clip", 2, 86400))
    cleanup_resolve()

    w["r2n_item_id"].setValue("not-a-real-id")
    pr = send_back.send(w)
    check("follow_clip gone: stored + warning", pr.placement == "stored" and any("isn't in the timeline" in x for x in pr.warnings), pr.warnings)
    w["r2n_item_id"].setValue(stamp.read_stamp(r).item_id)
    cleanup_resolve()

    timeline.SetCurrentTimecode("01:00:00:10")
    plan = send_back.preview(send_back.gather(w), send_back.SendOptions("playhead"))
    pr = send_back.send(w, send_back.SendOptions("playhead"))
    check("playhead: V2 at 86410, matches preview", (pr.track, pr.record_in) == (2, 86410) == (plan.track, plan.record_in))
    timeline.SetCurrentTimecode("01:00:00:00")
    cleanup_resolve()

    w["use_limit"].setValue(True); w["first"].setValue(110); w["last"].setValue(120)
    pr = send_back.send(w)
    check("limit 110-120: 86403-86413", (pr.record_in, pr.record_out) == (86403, 86413) and v_items(2)[0][3] == 0)
    w["use_limit"].setValue(False)
    cleanup_resolve()

    # # stamps and Write finding
    w2 = nuke.nodes.Write(inputs=[r], file=w["file"].value(), file_type="exr")
    check("unstamped write: uses Read's stamp", send_back.find_stamp(w2) == (stamp.read_stamp(r), []))
    for n in nuke.selectedNodes():
        n.setSelected(False)
    try:
        send_back.find_write()
        check("two writes: raises", False)
    except send_back.SendError as e:
        check("two writes: raises", "Select the one" in str(e))
    nuke.delete(w2)
    w["r2n_item_id"].setValue("other-clip")
    found, warns = send_back.find_stamp(w)
    check("stamp mismatch: warns, uses Write", found.item_id == "other-clip" and warns)
    w["r2n_item_id"].setValue(stamp.read_stamp(r).item_id)

    # # send action reports
    messages.clear()
    out = actions.run_send(send_back.SendOptions("stored"), w)
    check("run_send: summary dialog", out and len(messages) == 1 and "V2" in messages[0] and "VFX/test_tree_sc010_sh010" in messages[0], messages)
    cleanup_resolve()

    # #================ RETIMES
    check("not retimed: no curve, frame-for-frame", not shot.retimed and shot.frame_for_frame)
    timeline.SetCurrentTimecode("01:00:04:04")
    slow = resolve_bridge.get_shot()
    timeline.SetCurrentTimecode("01:00:00:00")
    check("50%: retimed, not a ramp, nearest", slow.retimed and not slow.is_ramp and slow.retime_nearest and abs(slow.speed - 50) < 0.01)
    check("50%: output 107-145", (slow.source_first, slow.source_last) == (107, 145), (slow.source_first, slow.source_last))
    check("50%: source frames 107, 107.5, 108...", [round(f, 3) for f in slow.retime_frames[:4]] == [107.0, 107.5, 108.0, 108.5], slow.retime_frames[:4])
    keys = shot_build.retime_keys(slow)
    check("50%: nearest keys floor", keys[:4] == [(107, 107.0), (108, 107.0), (109, 108.0), (110, 108.0)], keys[:4])

    nuke.scriptClear()
    res = shot_build.build(slow)
    k = res.kronos
    check("kronos: Frame mode, between Read and Write", k is not None and k["timing2"].value() == "Frame"
          and k.input(0) is res.read and res.write.input(0) is k)
    check("kronos: a key on every output frame", len(k["timingFrame2"].animation(0).keys()) == 39)
    check("kronos: values", [k["timingFrame2"].valueAt(f) for f in (107, 108, 109, 145)] == [107.0, 107.0, 108.0, 126.0],
          [k["timingFrame2"].valueAt(f) for f in (107, 108, 109, 145)])
    pts = [(x * 400 + .5, y * 300 + .5) for x in range(1, 10) for y in range(1, 7)]
    diff = max(abs(k.sample("rgba.green", x, y, frame=109) - res.read.sample("rgba.green", x, y, frame=108)) for x, y in pts)
    check("kronos: output 109 is source 108", diff < 1e-4, diff)
    check("retimed stamp", stamp.read_stamp(res.write).retimed == 1)
    check("root range = output frames", (nuke.root()["first_frame"].value(), nuke.root()["last_frame"].value()) == (107, 145))

    # # Send a retimed comp: first frame at the clip's start, tagged colour space
    w = res.write
    w["use_limit"].setValue(True); w["first"].setValue(107); w["last"].setValue(110)
    nuke.execute(w, 107, 110)
    pr = send_back.send(w)
    check("retimed send: follow clip -> 86500-86503", (pr.placement, pr.record_in, pr.record_out) == ("follow_clip", 86500, 86503),
          (pr.placement, pr.record_in, pr.record_out))
    placed = timeline.GetItemListInTrack("video", pr.track)[0]
    check("send tags Input Color Space", placed.GetMediaPoolItem().GetClipProperty("Input Color Space") == config.WRITE_RESOLVE_COLORSPACE,
          placed.GetMediaPoolItem().GetClipProperty("Input Color Space"))
    cleanup_resolve()

    # # Harry's speed ramp on Timeline 1 (read-only)
    tl1 = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
                if project.GetTimelineByIndex(i).GetName() == "Timeline 1"), None)
    ramp_item = next((i for t in range(1, tl1.GetTrackCount("video") + 1) for i in tl1.GetItemListInTrack("video", t) or []
                      if i.GetName().startswith("A010_07261620_C004")), None) if tl1 else None
    if ramp_item is None:
        print("SKIP  ramp: A010_07261620_C004 not on Timeline 1")
    else:
        ramp = resolve_bridge._shot_from_item(resolve, project, tl1, ramp_item)
        src0 = (ramp_item.GetSourceStartTime() - fcpxml_start(ramp_item)) * ramp.clip_fps + 1
        check("ramp: is a ramp, nearest", ramp.retimed and ramp.is_ramp and ramp.retime_nearest)
        check("ramp: first source frame matches Resolve", abs(ramp.retime_frames[0] - src0) < 0.05, (ramp.retime_frames[0], src0))
        check("ramp: output numbered from first source frame", ramp.source_first == int(ramp.retime_frames[0]))
        steps = [b - a for a, b in zip(ramp.retime_frames, ramp.retime_frames[1:])]
        check("ramp: fast, then eases up to 1x", steps[0] > 5 and min(steps) < 0.6 and abs(steps[-1] - 1) < 0.02, (round(steps[0], 2), round(min(steps), 2), round(steps[-1], 2)))

    # # Colour tag rules
    canon = dataclasses.replace(shot, file_path="/x/IMG.MP4", codec="H.265 Main 4:2:2 10 L5.1")
    check("tag: untagged non-BRAW warns", dataclasses.replace(canon, colorspace_from_project=True).colour_tag_warning())
    check("tag: BMD-tagged comp EXR is fine", dataclasses.replace(canon, file_path="/x/c_comp.%04d.exr", codec="").colour_tag_warning() is None)
    check("tag: Canon tagged Canon is fine", dataclasses.replace(canon, resolve_colorspace="Canon Cinema Gamut/Canon Log 3").colour_tag_warning() is None)
    check("tag: BRAW tagged BMD is fine", shot.colour_tag_warning() is None)
    check("tag: BRAW tagged Canon warns", dataclasses.replace(shot, resolve_colorspace="Canon Cinema Gamut/Canon Log 3").colour_tag_warning())

    # #================ TEMPLATE PLATES
    from Resolve2Nuke import template_plate, template_render
    plates_dir = os.path.join(os.environ["SHOT_ROOT"], "plates")
    shutil.rmtree(plates_dir, ignore_errors=True)
    t1 = template_plate.target("exr")
    check("template: first is v01, EXR path", t1.version == 1 and t1.path.endswith("test_tree_sc010_sh010_plate_v01/test_tree_sc010_sh010_plate_v01.%04d.exr"), t1.path)

    def resolve_state():
        # # Render queries (job list, presets, render mode) switch Resolve to the
        # # Deliver page, so read the page first and put it back afterwards.
        page = resolve.GetCurrentPage()
        state = _resolve_state(page)
        if page and resolve.GetCurrentPage() != page:
            resolve.OpenPage(page)
        return state

    def _resolve_state(page):
        presets = [p for p in project.GetRenderPresetList() if p.startswith("R2N_restore_")]
        temps = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)
                 if project.GetTimelineByIndex(i).GetName().startswith("R2N template ")]
        staging = [f for f in os.listdir(config.TEMPLATE_STAGING_DIR)] if os.path.isdir(config.TEMPLATE_STAGING_DIR) else []
        return (page, project.GetSetting("colorSpaceOutput"), project.GetCurrentRenderMode(), project.GetCurrentTimeline().GetName(),
                project.GetRenderJobList(), presets, temps, staging, project.IsRenderingInProgress(),
                project.GetSetting("timelineResolutionWidth"), project.GetSetting("timelineResolutionHeight"))

    resolve.OpenPage("edit")
    before = resolve_state()
    nuke.scriptClear()
    messages.clear()
    res = actions.run_pull(shot, shot_build.ShotOptions(render_template=True, template_format="exr"))
    tr = res.template
    check("template exr: Read made, no error dialog", tr is not None and not messages, messages)
    check("template exr: Resolve restored", resolve_state() == before, (before, resolve_state()))
    check("template exr: v01 files 107-145", tr is not None and len(os.listdir(os.path.dirname(t1.path))) == 39
          and (tr["first"].value(), tr["last"].value(), tr.format().width()) == (107, 145, 4096))
    if tr is not None:
        tr["raw"].setValue(True)
        hero = res.read
        hero["raw"].setValue(True)
        pts = [(x * 400 + .5, y * 300 + .5) for x in range(1, 10) for y in range(1, 7)]
        for f in (107, 145):
            d = sorted(abs(hero.sample(c, x, y, frame=f) - tr.sample(c, x, y, frame=f)) for x, y in pts for c in ("rgba.red", "rgba.green"))
            check(f"template exr: matches hero BRAW at {f}", d[len(d) // 2] < 0.002 and d[-1] < 0.01, (round(d[len(d) // 2], 5), round(d[-1], 5)))
        check("template exr: stamped, BMD Film", stamp.read_stamp(tr).item_id == shot.item_id and tr["colorspace"].value() == config.TEMPLATE_COLORSPACE)
    check("template: next is v02", template_plate.target("prores").version == 2)

    # # ProRes of the 50% clip at the clip's resolution: must line up with the Kronos
    nuke.scriptClear()
    messages.clear()
    res = actions.run_pull(slow, shot_build.ShotOptions(render_template=True, template_format="prores"))
    tr = res.template
    check("template prores: v02 .mov, starts at 107", tr is not None and tr["file"].value().endswith("_plate_v02.mov")
          and tr["frame_mode"].value() == "start at" and tr["frame"].value() == "107", messages)
    check("template prores: Resolve restored", resolve_state() == before, (before, resolve_state()))
    if tr is not None:
        pts = [(x * 400 + .5, y * 300 + .5) for x in range(1, 10) for y in range(1, 7)]
        for f in (107, 109, 140):
            d = sorted(abs(res.kronos.sample("rgba.green", x, y, frame=f) - tr.sample("rgba.green", x, y, frame=f)) for x, y in pts)
            check(f"template prores: matches Kronos at {f}", d[len(d) // 2] < 0.003, round(d[len(d) // 2], 5))

    # # Cancel during a real render: Resolve must stop and be fully restored (it hung once)
    calls = []

    def cancel_after_two():
        calls.append(1)
        return len(calls) > 2
    try:
        template_render.render_template(slow, "exr", True, cancelled=cancel_after_two)
        check("template cancel: raises", False)
    except resolve_bridge.ResolveError as e:
        check("template cancel: raises 'cancelled'", "cancelled" in str(e), str(e))
    check("template cancel: Resolve restored and idle", resolve_state() == before, (before, resolve_state()))

    # # Canon on Timeline 1 (read-only, via a temporary copy): clip res must stay colour managed.
    # # Custom timeline settings once turned colour management off and the plate came out as raw Canon Log 3.
    tl1 = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
                if project.GetTimelineByIndex(i).GetName() == "Timeline 1"), None)
    canon_item = next((i for i in (tl1.GetItemListInTrack("video", 1) if tl1 else []) or [] if i.GetName() == "IMG_5774.MP4"), None)
    if canon_item is None or canon_item.GetMediaPoolItem().GetClipProperty("Input Color Space") != "Canon Cinema Gamut/Canon Log 3":
        print("SKIP  canon template: IMG_5774.MP4 not on Timeline 1, or not tagged Canon Log 3")
    else:
        project.SetCurrentTimeline(tl1)
        canon_shot = resolve_bridge._shot_from_item(resolve, project, tl1, canon_item)
        before_tl1 = resolve_state()
        full = template_render.render_template(canon_shot, "exr", True)
        small = template_render.render_template(canon_shot, "exr", False)
        check("canon template: Resolve restored", resolve_state()[:7] == before_tl1[:7] and resolve_state()[8:] == before_tl1[8:],
              (before_tl1, resolve_state()))
        rf = nuke.nodes.Read(file=os.path.join(full.folder, os.path.basename(full.files[0])))
        rs = nuke.nodes.Read(file=os.path.join(small.folder, os.path.basename(small.files[0])))
        rf["raw"].setValue(True); rs["raw"].setValue(True)
        scaled = nuke.nodes.Reformat(inputs=[rf], type="to box", box_width=rs.format().width(), box_height=rs.format().height(), resize="distort")
        check("canon template: clip res is 3840x2160", (rf.format().width(), rf.format().height()) == (3840, 2160))
        pts = [(x * 190 + .5, y * 150 + .5) for x in range(1, 10) for y in range(1, 7)]
        d = sorted(abs(scaled.sample(c, x, y) - rs.sample(c, x, y)) for x, y in pts for c in ("rgba.red", "rgba.green"))
        check("canon template: clip res is colour managed (matches timeline res)", d[len(d) // 2] < 0.01, round(d[len(d) // 2], 5))
        shutil.rmtree(full.folder, ignore_errors=True)
        shutil.rmtree(small.folder, ignore_errors=True)
        project.SetCurrentTimeline(timeline)

    # #================ GRADE LUT
    from Resolve2Nuke import grade_lut
    g1 = grade_lut.lut_path()
    check("grade: first LUT is v01 in plates", g1.endswith("plates/test_tree_sc010_sh010_grade_v01.cube"), g1)
    page_before = resolve.GetCurrentPage()
    nuke.scriptClear()
    messages.clear()
    res = actions.run_pull(shot, shot_build.ShotOptions(include_grade=True))
    check("grade: ungraded clip -> quietly no group, no LUT left", res.grade is None and not messages and not os.path.exists(g1), messages)
    check("grade: Resolve back on its page", resolve.GetCurrentPage() == page_before, (page_before, resolve.GetCurrentPage()))
    graded_item = next((i for i in (tl1.GetItemListInTrack("video", 1) if tl1 else []) or []
                        if i.GetName().startswith("A001") and i.GetStart() == 86520), None)
    if graded_item is None:
        print("SKIP  grade LUT: no A001 instance at 86520 on Timeline 1")
    else:
        graded_shot = resolve_bridge._shot_from_item(resolve, project, tl1, graded_item)
        nuke.scriptClear()
        messages.clear()
        res = actions.run_pull(graded_shot, shot_build.ShotOptions(include_grade=True))
        grp = res.grade
        if grp is None and not messages:
            print("SKIP  grade LUT: the A001 instance at 86520 isn't graded")
        else:
            check("grade: group made, no error", grp is not None and not messages, messages)
            check("grade: LUT v01 written, not an identity", os.path.isfile(g1) and not resolve_bridge.cube_is_identity(g1))
            check("grade: branch off the Read, Write still on the Read", grp.input(0) is res.read and res.write.input(0) is res.read)
            check("grade: group shows the LUT file", grp["vfield_file"].value() == g1, grp["vfield_file"].value())
            check("grade: next LUT is v02", grade_lut.lut_path().endswith("_grade_v02.cube"))
            check("grade: Resolve back on its page", resolve.GetCurrentPage() == page_before)
            project.SetCurrentTimeline(tl1)
            plate = template_render.render_template(graded_shot, "exr", True)
            project.SetCurrentTimeline(timeline)
            tp = nuke.nodes.Read()
            tp["file"].fromUserText(plate.files[0].rsplit(".", 2)[0] + f".%08d.exr {graded_shot.source_first}-{graded_shot.source_last}")
            tp["colorspace"].setValue(config.TEMPLATE_COLORSPACE)
            to_bmd = lambda n: nuke.nodes.OCIOColorSpace(inputs=[n], in_colorspace="scene_linear", out_colorspace="BMDFilm WideGamut Gen5")
            a, b, c0 = to_bmd(grp), to_bmd(tp), to_bmd(res.read)
            pts = [(x * 400 + .5, y * 300 + .5) for x in range(1, 10) for y in range(1, 7)]
            f = graded_shot.source_first
            d = sorted(abs(a.sample(ch, x, y, frame=f) - b.sample(ch, x, y, frame=f)) for x, y in pts for ch in ("rgba.red", "rgba.green", "rgba.blue"))
            d0 = sorted(abs(c0.sample(ch, x, y, frame=f) - b.sample(ch, x, y, frame=f)) for x, y in pts for ch in ("rgba.red", "rgba.green", "rgba.blue"))
            check("grade: group matches Resolve's graded plate", d[len(d) // 2] < 0.002 and d0[len(d0) // 2] > 0.01,
                  (round(d[len(d) // 2], 5), "ungraded:", round(d0[len(d0) // 2], 5)))
            shutil.rmtree(plate.folder, ignore_errors=True)
    saved_root = os.environ.pop("SHOT_ROOT")
    try:
        grade_lut.lut_path()
        check("grade not hozed: raises", False)
    except grade_lut.GradeError as e:
        check("grade not hozed: raises", "hoz" in str(e))
    os.environ["SHOT_ROOT"] = saved_root

    # # Not hozed: a clear error, no render
    saved_root = os.environ.pop("SHOT_ROOT")
    messages.clear()
    out = actions.run_template(shot, shot_build.ShotOptions(render_template=True))
    check("template not hozed: error dialog, no Read", out is None and messages and "hoz" in messages[0], messages)
    os.environ["SHOT_ROOT"] = saved_root
    shutil.rmtree(plates_dir, ignore_errors=True)

    # #================ BIN EVALUATION
    check("bin: hozed", send_back.evaluate_bin("VFX/[getenv SHOT_NAME]") == ("VFX/test_tree_sc010_sh010", None))
    path, why = send_back.evaluate_bin("VFX/[getenv R2N_UNSET_VAR]")
    check("bin: unset var -> VFX + reason", path == "VFX" and "R2N_UNSET_VAR" in why, (path, why))
    check("bin: tidy slashes", send_back.evaluate_bin(" VFX//a/ ") == ("VFX/a", None))
    check("bin: empty -> VFX", send_back.evaluate_bin("  ")[0] == "VFX")
    check("bin: bad tcl -> VFX", send_back.evaluate_bin("VFX/[oops")[0] == "VFX")

    # #================ SETTINGS
    QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).clear()
    check("settings: defaults when empty", settings.load_send_options() == send_back.SendOptions())
    settings.save_send_options(send_back.SendOptions("playhead", "Comps/[getenv SHOT_NAME]"))
    settings.save_shot_options(shot_build.ShotOptions(set_ocio=False, fps_source="clip", handles=8))
    so, po = settings.load_send_options(), settings.load_shot_options()
    check("settings: send remembered", (so.placement, so.bin) == ("playhead", "Comps/[getenv SHOT_NAME]"))
    check("settings: fps/handles remembered, checkboxes not", (po.fps_source, po.handles, po.set_ocio) == ("clip", 8, True))
    QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).setValue("send/placement", "nonsense")
    check("settings: bad value -> default", settings.load_send_options().placement == "follow_clip")
    config.REMEMBER_SETTINGS = False
    check("settings: off -> defaults", settings.load_send_options() == send_back.SendOptions())
    config.REMEMBER_SETTINGS = True
    QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).clear()

    # #================ MISC
    from Resolve2Nuke import dialogs
    check("dialogs: import headless", hasattr(dialogs, "open_pull_dialog") and hasattr(dialogs, "open_send_dialog"))

    class FakeMPI:
        def GetClipProperty(self, key=None):
            p = {"Start": "0", "File Path": "/x/a.braw"}
            return p.get(key) if key else p

    class FakeItem:
        def __init__(self, start, left): self.start, self.left = start, left
        def GetUniqueId(self): return "id1"
        def GetStart(self): return self.start
        def GetLeftOffset(self): return self.left
        def GetMediaPoolItem(self): return FakeMPI()
        def GetTrackTypeAndIndex(self): return ["video", 1]

    class FakeTimeline:
        def __init__(self, item): self.item = item
        def GetTrackCount(self, t): return 1
        def GetItemListInTrack(self, t, i): return [self.item]

    comp = resolve_bridge.CompPlacement("/x/c.%04d.exr", 107, 145, "p", "t", "id1", 1, 86400, 107)
    check("follow maths: moved +100, trimmed 5", resolve_bridge._anchor(FakeTimeline(FakeItem(86600, 111)), comp, "follow_clip", [])[1] == 86595)
    check("follow maths: head extended 10", resolve_bridge._anchor(FakeTimeline(FakeItem(86390, 96)), comp, "follow_clip", [])[1] == 86400)
    check("seq path", resolve_bridge._nuke_path("/a/r.[0107-0145].exr") == ("/a/r.%04d.exr", True))

    # # A 200% clip on V2 (FCPXML nests it in the V1 clip as lane 1). Added last: it occupies V2.
    while timeline.GetTrackCount("video") < 2:
        timeline.AddTrack("video")
    braw_mpi = timeline.GetItemListInTrack("video", 1)[0].GetMediaPoolItem()
    fast = mp.AppendToTimeline([{"mediaPoolItem": braw_mpi, "startFrame": 120, "endFrame": 150, "trackIndex": 2, "recordFrame": 86410}])[0]
    fast.SetSpeed({"Percentage": 200.0})
    timeline.SetCurrentTimecode("01:00:00:12")
    v2 = resolve_bridge.get_shot()
    timeline.SetCurrentTimecode("01:00:00:00")
    check("V2 200%: picked, lane read, 121, 123, 125", v2.track_index == 2 and v2.retimed and [round(f, 3) for f in v2.retime_frames[:3]] == [121.0, 123.0, 125.0],
          (v2.track_index, v2.retime_frames[:3] if v2.retimed else None))

    calls = []

    class FakeMenu:
        def addMenu(self, name, **kw): calls.append(("addMenu", name)); return self
        def addCommand(self, name, cmd=None, **kw): calls.append(("addCommand", name, cmd))
        def addSeparator(self): pass
    nuke.menu = lambda name: FakeMenu()
    exec(open("/home/harry/Code/Resolve2Nuke/menu.py").read(), {"__name__": "menu"})
    names = [c[1] for c in calls if c[0] == "addCommand"]
    check("menu: pull and send commands", "Pull shot from Resolve" in names and "Send comp to Resolve" in names, names)

except Exception:
    traceback.print_exc()
    check("no exception", False)
finally:
    cleanup_resolve()
    timeline.SetCurrentTimecode("01:00:00:00")
    QtCore.QSettings(config.SETTINGS_ORG, config.SETTINGS_APP).clear()
    print("V tracks after cleanup:", timeline.GetTrackCount("video"), "| bins:", snapshot()[2])
    project.SetCurrentTimeline(user_timeline)
    print("back on timeline:", project.GetCurrentTimeline().GetName())

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
