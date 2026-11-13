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
- Unique IDs survive a Resolve restart.
- The MCP `run_script` has a 10 s limit. Start renders in one call and poll in another, and restore settings in the polling call.

## Roadmap

- [ ] GUI for pull (`ShotOptions` as checkboxes, with fps source greyed out when the clip and timeline match, plus a confirmation) and for send (placement mode, plus a bin line edit defaulting to `VFX/[SHOT_NAME]` so many shots and versions stay tidy; nested bins need creating level by level).
- [ ] Warn when the Resolve clip has a speed change, because the source range is wrong then.
- [ ] Versioned Write paths (the `v01` is hard-coded). Harry plans a better path setter.
- [ ] More pixel-tested colour mappings (Canon R6 / Canon Log 3 next).
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
- **Testing:** run headless Nuke 17.1 (`-ti`) with Resolve open on the VFX Test project. The test scripts lived in the session scratchpad; they're worth moving into a `tests/` folder.
