# Final Cut Pro workflow guide

## Why FCPXML (and not an in-app plugin)?

Final Cut Pro does not expose a public API for manipulating timelines from
a plugin — Apple's supported mechanism for programmatically *building*
timelines is **FCPXML**, the same interchange format FCP itself exports.
EditSync generates FCPXML v1.11, which current FCP versions import
directly. (FCP "Workflow Extensions" exist but are limited to panels that
ultimately hand FCPXML to the app — the file this tool produces is the
same payload, minus the App Store packaging.)

The upside of this design is portability: the sync engine doesn't know or
care about FCP, so the identical timeline exports to Premiere and OTIO.

## Step by step

1. **Offload footage.** Copy the DJI files and the Meta glasses files into
   one folder (subfolders are fine — the scan is recursive). Keep them at
   their final location; the generated project references the files in
   place, so moving them later means relinking in FCP.

2. **(Optional) check classification:**

   ```sh
   editsync probe ./footage
   ```

   Landscape footage becomes the primary storyline; portrait footage
   becomes overlays. If something is misclassified, override with globs:
   `--primary "DJI_*" --overlay "*.glasses.mp4"`.

3. **Sync and export:**

   ```sh
   editsync sync ./footage -o myvideo.fcpxml --report sync.json
   ```

   Read the printed report: it lists where every Meta clip landed and its
   confidence, plus warnings for anything skipped or suspicious.

4. **Import into FCP.** Drag `myvideo.fcpxml` onto the Final Cut Pro icon,
   or File → Import → XML. You get an "EditSync" event containing the
   project.

5. **Edit.** The timeline you get:
   - **Primary storyline**: the DJI clips in order.
   - **Connected clips above**: each Meta clip at its synced spot, on its
     own lane. They are ordinary connected clips — trim, extend, slip, or
     delete them like anything else. Extending one keeps sync because the
     clip's media is already aligned.
   - **DJI audio ducked** under each Meta clip with 0.25 s fades (volume
     keyframes on the DJI clip). If you delete a Meta clip, select the DJI
     clip and remove/adjust its volume keyframes, or re-run EditSync.
   - **Markers**: each Meta clip carries a green marker recording what it
     matched and how confidently. Low-confidence placements get a **red
     to-do marker** — check those spots by ear.
   - **Roles**: DJI audio is `dialogue.DJI`, Meta audio is `dialogue.Meta`
     (Modify → Assign Roles to see them; use the Timeline Index to solo or
     mute a whole camera).

## Tips

- **Vertical framing**: `--overlay-style blur-bg` gives the classic
  "sharp vertical clip over a blurred background" look — the main-camera
  clip underneath gets a keyframed Gaussian Blur that ramps in only while
  an overlay is on top (strength via `--blur-amount 0-100`). Delete an
  overlay and you can remove the corresponding blur keyframes from the
  clip below (Video inspector), or re-run EditSync. Other styles:
  `center` (pillarboxed over the sharp wide shot), `fill` (punched-in
  full frame), `pip-left`/`pip-right` picture-in-picture; every value is
  just a starting Transform/effect you can tweak in the inspector.
- **Keep DJI audio under overlays**: `--duck -18` instead of the default
  full duck, or `--duck off` to leave mixing entirely to you.
- **Long shoots with many files**: `--search-window 120` uses the files'
  creation timestamps to only search plausible regions — much faster and
  avoids false matches when audio repeats (music, chants).
- **DaVinci Resolve**: the same `.fcpxml` imports via
  File → Import → Timeline, and the `.otio` export works too.
