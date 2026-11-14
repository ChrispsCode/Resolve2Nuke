# Resolve2Nuke working notes

Working notes for Resolve2Nuke: background, decisions and why, what was rejected, the roadmap, and a guide for whoever picks it up next.

## Background

Harry grades in DaVinci Resolve (project colour management: input Blackmagic Design Film Gen 5, timeline DaVinci WG/Intermediate, output Rec.709 Gamma 2.4) and comps in Nuke. The old round trip exported an EXR plate from Resolve in DaVinci Wide Gamut / Linear, comped it, and brought it back with colour space transform nodes and "bypass colour management". That wastes disk space on plates and takes a lot of manual setup.

Resolve2Nuke comps on the **original BRAW** in Nuke instead, and sends the comp back so it sits over the BRAW on the timeline with no colour work in Resolve.

## Phases

### 1. Pull shot from Resolve (v0.1.0, commit a47b896)

`Nodes > Resolve2Nuke > Pull shot from Resolve` takes the clip **selected** in Resolve, or else the topmost enabled clip under the **playhead**, and:

- sets the root: OCIO config, fps, format, frame range;
- creates a Read (colorspace from Resolve's input colour space, BRAW decode from camera metadata);
- creates an EXR Write on the hoz path.

Every root change is a toggle in `shot_build.ShotOptions`, ready for the GUI. Bringing in secondary plates means switching the root settings off.

### 2. Send comp to Resolve (v0.2.0)

`Nodes > Resolve2Nuke > Send comp to Resolve` takes the selected Write (or the only one) and:

1. checks every frame of its range exists;
2. imports the frames into a top-level **VFX** bin, reusing the clip if that file path is already there;
3. places them on the **first free track above** the original clip, adding tracks if needed. It never replaces anything, so a re-send can be compared with the old comp.

**Placement modes** (`send_back.SendOptions.placement`, default from `config.DEFAULT_PLACEMENT`):

- `follow_clip` (default): finds the original clip by its Resolve unique ID wherever it is now, and aligns by source frame: `record = item.GetStart() + (comp_first - item_source_first(item))`. So moved, trimmed or extended clips still land in sync. If the clip is gone (deleted, or replaced with a new instance), it falls back to `stored` with a warning.
- `stored`: the record position saved at pull time.
- `playhead`: the comp's first frame goes at the playhead, above the topmost clip there. This is for when the edit changed in a way `follow_clip` can't follow.

**Stamps:** pull adds a **Resolve2Nuke** tab (`r2n_*` knobs, see `stamp.py`) to the Read *and* the Write. On send-back the Write's own stamp wins. Walking up the B pipe to a stamped Read is only a fallback, for hand-made Writes. If both exist and name different clips, it warns and uses the Write's.

### 3. Dialogs (v0.3.0)

In the GUI, both menu commands open a non-modal dialog (`dialogs.py`). With `options` passed, or headless, `pull_shot()` and `send_comp()` still run straight away.

- **Pull dialog:**
  - a summary of the shot;
  - checkboxes for each `ShotOptions` root setting, always starting ticked;
  - fps source (greyed out when the clip and timeline rates match);
  - handles;
  - Refresh / Pull / Cancel.
- **Send dialog:**
  - the Write, its frame check and its stamp;
  - placement mode;
  - a **bin** line edit, defaulting to `config.DEFAULT_BIN` = `VFX/[getenv SHOT_NAME]`. TCL is evaluated, "/" makes nested bins created level by level, and it falls back to `VFX` if evaluation fails (e.g. not hozed);
  - a **live preview** ("→ V3 at 01:00:00:00, 39 frames") from `resolve_bridge.plan_placement()`. That's a dry run that changes nothing in Resolve, and `place_comp()` carries out the same plan.
- **Shared logic:** `actions.py` holds the run-and-report logic used by both the dialogs and the direct calls.
- **Remembered settings:** `settings.py` remembers fps source, handles, placement and bin per user in QSettings, when `config.REMEMBER_SETTINGS` is True. The checkboxes always start ticked, so a secondary-plate pull never leaves the next main-plate pull half set up.

### 4. Retimes, colour tagging, small fixes (v0.4.0)

- **Retime pull:**
  - Every pull exports the current timeline as FCPXML to a temp folder (`fcpxml.py`, no nuke or Qt imports), and finds the clip by record time and track.
  - A `<timeMap>` means the clip is retimed. Its curve is evaluated at every output frame: linear corners, and `smooth2` keys as a quadratic Bézier from `inTime` before the key to `outTime` after it.
  - The pull adds a **Kronos** between the Read and the Write, with `timing2 = Frame` and a `timingFrame2` key on every frame (linear). Keys are whole frames (floored, like Resolve) when Resolve's retime process is Nearest (the clip's, or the project's `imageRetimeInterpolation` when the clip says "use project"), and fractional otherwise.
  - Output frames are numbered from the cut's first source frame. The Read keeps the whole clip.
- **Retimed send:**
  - The comp has the retime baked in, so `follow_clip` puts its first frame at the clip's start: `record = clip start + (frame − first)`.
  - The stamp's `retimed` field says which kind of comp it is. (Old stamps were allowed to break, since none are in production.)
- **The send tags the comp's Input Color Space** as `config.WRITE_RESOLVE_COLORSPACE` ("Blackmagic Design Film Gen 5", matching the Write). The comp then doesn't depend on the project's input colour space, so a Canon-primary project works the same way.
- **Pull dialog:**
  - a Retime row (orange when retimed: speed, or ramp with its average);
  - a colour-tag check (orange): an untagged non-BRAW clip, a Blackmagic tag on a non-BRAW file, or a non-Blackmagic tag on a BRAW;
  - the fps line now *checks* frame-for-frame playback, using (source end − source start time) × clip fps against the duration, instead of always warning.
- **`tests/gui_preview.py`** renders both dialogs offscreen to PNGs (using Nuke's bundled Python, a stubbed `nuke` and fake data). It caught clipped warnings and a squeezed form label, now fixed with word-wrapped labels and middle-elided paths.

### 5. Template plates (v0.5.0)

A **Template plate** option in the pull dialog (off by default, only when hozed) has Resolve render the clip **as it plays in the edit** (grade, sizing, retime). It's used as a reference, or as the hero plate when Nuke can't read the camera file (Canon R6 HEVC 4:2:2). The hero Read is always created anyway, even if Nuke can't read it, ready for a separate conversion tool.

- **Resolve side (`template_render.py`, no nuke import):**
  1. Duplicate the current timeline and delete everything in the copy except the clip, matched by track and record start because the copy's IDs are new.
  2. Optionally render at the clip's resolution, by changing the **project's** timeline resolution, which the copy inherits. Custom settings on the copy would turn its colour management off; see the quirks below.
  3. Save the Deliver settings as a temporary preset, then switch the **project's** output colour space to BMD Film Gen 5 (a timeline can't hold its own: `SetSetting("colorSpaceOutput")` on a timeline returns False).
  4. Render the record range in single-clip mode, with `ClipStartFrame = source_first` so EXR frames carry the comp's frame numbers, into a staging folder under `~/DaVinci Resolve Media/Resolve2Nuke/` (Media Storage). Progress and Cancel go through `nuke.ProgressTask` / `StopRendering()`.
  5. Everything is restored in a `finally`, once Resolve has really stopped rendering: output colour space, resolution, render mode, Deliver preset, job, the copy, and the current timeline. A failed or cancelled render deletes its staging folder.
- **Nuke side (`template_plate.py`):**
  - The next version counts across both formats: `$SHOT_ROOT/plates/<SHOT_NAME>_plate_v##/<SHOT_NAME>_plate_v##.%04d.exr` or `$SHOT_ROOT/plates/<SHOT_NAME>_plate_v##.mov`.
  - It moves the render out of staging, renaming Resolve's 8-digit frames to `%04d`, and never overwrites an existing version.
  - It creates a stamped Read (BMDFilm WideGamut Gen5) next to the hero Read. A movie Read uses `frame_mode` "start at" `source_first`, because movies always read as frames 1–N.
  - It's its own undo step, after the pull's.
- **Formats:** EXR (half, ZIP, lossless; 1.1 GB for 39 frames of 4K) or ProRes 4444 XQ (12-bit; 0.33 GB). Both are encoded as BMD Film Gen5 / WG Gen5.
- **Verified 2026-11-14:**
  - An EXR template of an ungraded BRAW matches Nuke's own BRAW decode (median 0.0006, max 0.0024).
  - ProRes 4444 XQ matches too (12-bit rounding, max about 0.005).
  - A ProRes template of a 50% clip matches the Kronos frame for frame (median 0.0002), so Resolve's Nearest retime and our keys agree.
  - The 0.856× remap seen with DaVinci Intermediate output doesn't happen with BMD Film output.

### 6. Grade LUT (v0.6.0)

**Include grade (LUT)** in the pull dialog's Script settings (off by default, hozed only) gives a reference view of the clip's Resolve grade. It's not for baking: the grade is copied onto the comp in Resolve.

- **Resolve side:** `TimelineItem.ExportLUT` writes a 65-point `.cube` to `$SHOT_ROOT/plates/<SHOT_NAME>_grade_v##.cube`, versioned, since ExportLUT won't overwrite. It only works on the **Color page**, so the tool switches there and back. An identity LUT means there's no grade: the file is deleted and the artist is told.
- **What the LUT is:** **the grade only**, applied in the timeline working space (DaVinci WG/Intermediate). An ungraded clip exports an exact identity. Verified 2026-11-14: hero BRAW → DaVinci Intermediate → LUT matches Resolve's graded template plate to a median of 0.0006 (0.02 off if applied in BMD Film, 0.035 with no LUT).
- **The dialog ticks it by default** (it sits above Create Write). An ungraded clip is then skipped quietly, with a log line. Scripted `ShotOptions` keep `include_grade=False` unless asked.
- **Nuke side (`grade_lut.py`):** a **ResolveGrade** Group holding OCIOColorSpace (scene_linear → DaVinci Intermediate WideGamut) → Vectorfield → OCIOColorSpace (back), because Vectorfield's own colorspace knobs only list Nuke's 26 built-in curves. The LUT file is exposed on the Group. The Group branches off the Read (or the Kronos) and is never in the Write's pipe.

## Decisions and why

- **Nuke 17.1+ only.** 17.1's BRAW reader uses SDK 5.1 (15.2's uses 2.2, Resolve 21 uses 5.x). `DaVinciResolveScript` imports in-process in 17.1, while in 15.2 Resolve's `fusionscript.so` fails on a libc++ symbol clash. `menu.py` prints a red warning in 15.
- **Colour: BMD Film Gen5 end to end.**
  - Read: `BMDFilm WideGamut Gen5`, `decode_using` = Camera Metadata.
  - Comp: ACEScg (root config `fn-nuke_studio-config-v2.2.0_aces-v1.3_ocio-v2.4`).
  - Write: EXR, 16-bit half, `BMDFilm WideGamut Gen5`.
  - In Resolve the EXR stays on the project input colour space. No CSTs, no bypass.
- **Native frame numbers, not 1001.** The comp's frames are the plate's own Nuke frame numbers (107–145 in the test shot), with 0 handles by default. Handles and extensions become simple arithmetic, and the Read keeps the whole clip.
- **fps from the timeline by default** (`fps_source="timeline"`), so frames go back 1:1. If the clip's fps differs, Resolve retimes it and the mapping breaks, so `build()` warns.
- **Write path** (temporary): `[getenv SHOT_ROOT]/renders/comp/c_[getenv SHOT_NAME]_comp_v01.%04d.exr`, with `create_directories` on (hoz may have cleaned the folder away).
- **Unknown colour space → dialog.** Only pixel-tested mappings go in `config.RESOLVE_TO_OCIO`.
- **Re-sends go on a new track, never replace**, so old and new comps can be compared (confirmed by Harry).

## Verification (2026-11-12, Resolve 21.1, Nuke 17.1v1)

- **Round-trip pixel test.**
  - Method: the BRAW was read in Nuke and written as EXR (no comp), then placed on V2 over the BRAW. Resolve rendered with V2 on and off (4K, output temporarily DaVinci WG/Intermediate).
  - Result: same frame median 0.0006, max 0.0028. Neighbouring frames differed by about 0.008 median.
  - Harry confirmed by eye: Difference composite mode came out black, and a grade looked the same on both clips.
- **Frame mapping:** the Nuke BRAW frame = `GetLeftOffset()` + 1. This was checked against the viewer's source timecode (12:00:07:12 = frame 107).
- **Unexplained, harmless here:** Resolve's render to DaVinci WG/Intermediate doesn't match Nuke's BMDFilm→DI conversion. Resolve comes out at about 0.856 × Nuke + 0.09. Nuke's OCIO conversion and the BRAW SDK's own DI output agree, and the DRT and grade aren't the cause. It only matters if EXRs are ever written in DWG/DI instead of BMD Film.

## Resolve API quirks (Resolve 21.1)

- `TimelineItem.GetSourceStartFrame()` is off by one for BRAW. Use `GetLeftOffset()`.
- `MediaPool.AppendToTimeline` with an image sequence takes **file frame numbers** as `startFrame`/`endFrame`, and `endFrame` is **exclusive** (107–145 is `107, 146`). Passing 0-based numbers gives a clip that holds one frame.
- A sequence's `File Path` property reads `name.[0107-0145].exr`. `Start` is the first file number for sequences and 0 for movie files.
- **Rendering** needs a Media Storage location as the target (`~/Videos` works; the scratchpad and `~/Documents` don't). **Importing** from anywhere works.
- `Timeline.SetTrackEnable(False)` didn't affect a render. `TimelineItem.SetClipEnabled(False)` did.
- No API moves a timeline item, so `follow_clip` is tested with fake items, plus a GUI check.
- **`MediaPool.AppendToTimeline` always adds to the *current* timeline**, whatever timeline object you have in hand. Switch with `project.SetCurrentTimeline()` first, or the clip lands on the wrong timeline.
- **Retimed clips:** `GetLeftOffset()` is counted in timeline frames (a 50% clip from source 106 reports 212). `GetSpeed()` gives the average speed for a ramp. `GetSourceStartTime()` is right. The full curve is only in the FCPXML export (OTIO and FCP 7 XML only give a constant speed).
- **FCPXML layout:**
  - V1 clips sit in `<spine>`, with absolute offsets.
  - V2+ clips are nested inside the V1 clip they overlap, as `lane="n"`, with an offset in the parent's local time: absolute = parent offset + (offset − parent start).
  - `<timeMap>` points pair the clip's local time with source time, in the source's own seconds (the asset's `start` = the source's start timecode).
- **Input colour space** is the clip property `Input Color Space`. Tagging in the Color page (right-click the thumbnail) sets it, and `SetClipProperty` can set it too.
- **The mapped Media Storage** (2026-11-14): `~/Videos`, `~/DaVinci Resolve Media`, `~/Documents/DevTest`.
- **Never switch a timeline to custom settings for a render** (`SetSetting("useCustomSettings", "1")`). It changes that timeline's colour science from `davinciYRGBColorManagedv2` to plain `davinciYRGB`, i.e. unmanaged, and the render passes the decode straight through. A BRAW still looks right (its decode *is* BMD Film), which hid the bug. A Canon came out as raw Canon Log 3, read as BMD Film: "crunchy and contrasty" (Harry, 2026-11-14). Colour science and the timeline colour space can be set back on a custom timeline, but its output and input colour spaces can't (`SetSetting` returns False). Template plates at the clip's resolution therefore change the *project's* timeline resolution and restore it, and refuse to render from a timeline that isn't colour managed.
- **`StopRendering()` is asynchronous.** Wait until `IsRenderingInProgress()` is False before touching the job or the timeline. Deleting the timeline being rendered left Resolve hung, and it needed a force quit (2026-11-14). The template cleanup now waits up to 30 s, and if Resolve still hasn't stopped, it leaves the job and the copy in place and says so.
- **Resolve's page matters to the API:**
  - `TimelineItem.ExportLUT` returns False unless the **Color** page is open.
  - **Render calls switch Resolve to the Deliver page** (`AddRenderJob` does, and so did one of the render queries before it).
  - `GetSelectedClips()` depends on the page: on Color it returns the current colour clip, and on Deliver, Deliver's current clip. So a pull made while Resolve sat on Deliver after a template render would take a stale clip for the artist's selection.
  
  The tool reads the page **before** any render call and puts it back. Tests that query render state must do the same.
- **`Timeline.DuplicateTimeline()` makes the copy the current timeline.** Rendering needs it current anyway.
- **`ClipStartFrame`** in `SetRenderSettings` sets an image sequence's first frame number (8-digit padding). Movies ignore it, and carry the record timecode instead.
- **The render state to restore:** `GetSetting("colorSpaceOutput")`, `GetCurrentRenderMode()`, and the Deliver settings via `SaveAsNewRenderPreset` / `LoadRenderPreset` / `DeleteRenderPreset`.
- **Resolve on Linux can encode ProRes** (422 to 4444 XQ) and DNxHR 444. Nuke 17.1 on Linux can't decode HEVC 4:2:2 10-bit (Canon R6).
- Unique IDs survive a Resolve restart.
- The MCP `run_script` has a 10 s limit. Start renders in one call and poll in another, and restore settings in the polling call.

## Roadmap

- [ ] GUI for pull (`ShotOptions` as checkboxes, with fps source greyed out when the clip and timeline match, plus a confirmation) and for send (placement mode, plus a bin line edit defaulting to `VFX/[SHOT_NAME]` so many shots and versions stay tidy; nested bins need creating level by level).
- [x] Retimes: Kronos (Frame) from Resolve's curve (v0.4.0).
- [x] Template plates (v0.5.0).
- [x] Grade LUT reference branch (v0.6.0).
- [ ] A separate tool (another repo) to convert camera files Nuke can't read, for full-range hero plates.
- [ ] Versioned Write paths (the `v01` is hard-coded). Harry plans a better path setter.
- [ ] More pixel-tested colour mappings. Canon may not need one: template plates come out as BMD Film.
- [ ] Drop-frame timelines for playhead pick and placement.
- [ ] Optional system-Python fallback for the Resolve connection.

## Developer guide

- **Layout:**
  - `menu.py` (guarded, Nuke 17+) → `nuke_menu.build()`.
  - `resolve_bridge.py` talks to Resolve and has **no nuke import**.
  - `shot_build.py` builds the script for a pull.
  - `send_back.py` is the Nuke side of the send.
  - `stamp.py` handles the stamp knobs.
  - `config.py` holds the constants.
- **Entry points** (also the menu commands, so keep the names): `Resolve2Nuke.pull_shot()`, `Resolve2Nuke.send_comp()`. Reload with `Resolve2Nuke.reload()`.
- **Don't name a module after a package-level function.** `send_comp()` shadowed a `send_comp` module, which was renamed `send_back`.
- **Testing:** run headless Nuke 17.1 (`-ti`) with Resolve open on the VFX Test project. The test script builds its own **"R2N Test"** timeline each run (the BRAW, source 107–145, at 01:00:00:00), and only deletes the bins it creates. It never touches the timeline you're working on. (A first version cleaned up all of V2 and above and the whole VFX bin on the current timeline, and wiped Harry's GUI-test comps.) For a movie clip, `AppendToTimeline` takes 0-based `startFrame` and an exclusive `endFrame`, so (106, 145) gives left offset 106 and 39 frames.
- Widgets can't be created headless. Keep logic in the Qt-free modules (`send_back`, `shot_build`, `settings`, `actions`), and leave the dialogs to a GUI checklist.
