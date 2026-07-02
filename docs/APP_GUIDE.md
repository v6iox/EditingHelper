# The EditSync app

EditSync is a small desktop app. You drop in everything you filmed, press
one button, and get a file you drag into Final Cut Pro — with all your
glasses clips already matched to the right moment by sound, each on its
own layer.

## Getting the app

Download from the project's **GitHub Releases** page:

- **Mac**: `EditSync.dmg` — open it and drag EditSync into Applications.
- **Windows**: `EditSync-windows.zip` — unzip anywhere and open
  `EditSync.exe`.

The app includes everything it needs (ffmpeg is bundled) — nothing else to
install, no terminal, ever.

> **First launch on a Mac**: releases built after code signing was set
> up (see docs/SIGNING.md) open with a normal double-click. If you have
> an older unsigned build and macOS warns you, **right-click the app →
> Open → Open** — one time only.

*(Maintainers: releases are built automatically — push a tag like `v0.1.0`
or run the "Build desktop app" workflow on GitHub Actions. To build
locally on a Mac: `./packaging/build_macos.sh`.)*

## Using it

1. **Drop your footage** — everything from the shoot at once: the DJI
   files and the Meta glasses files, or the whole folder. The app figures
   out which is which (horizontal = main camera, vertical = overlay) and
   labels each file.

2. **Pick your options** — all in plain language:
   - **Project name** — what the project will be called in Final Cut.
   - **Opening title** — type a title (the panel or procedure) and a
     description under it (year/make/model) and the video starts on a
     clean white card that fades out to reveal the footage. Pick one of
     four looks from the live previews — they show your actual text.
     Two sliders control how long it stays and how fast it fades.
     Leave the title empty for no card. (The card exports to Final Cut
     Pro; Premiere's interchange format can't carry titles, and the app
     will remind you if that combination comes up.)
   - **Vertical clips look like** — **Blurred background** (the default:
     your wide shot stays behind the vertical clip, softly blurred, with
     a slider for how much blur), centered, fill the frame, or small in a
     corner. (Just a starting point; you can change it in Final Cut.)
   - **While a glasses clip plays** — mute the main camera, turn it down,
     or leave it alone.
   - **Background music** (off unless you turn it on) — drop a song
     (mp3, wav, m4a…) in with your footage and check "Loop my music
     file quietly under the whole video". It repeats for the full
     length of the video on its own layer underneath, at background
     level (there's a volume slider), so your voice stays clear. A
     second switch, also off by default, **silences the music while a
     glasses clip plays** and fades it back in after.
   - **Save for** — Final Cut Pro, Premiere Pro, and/or DaVinci Resolve.

3. **Press "Sync my footage"**, choose where to save, and wait — the app
   listens to all the audio and finds where every clip belongs.

4. **Drag the `.fcpxml` file into Final Cut Pro.** Your timeline appears
   as a new project:
   - main camera on the bottom,
   - every glasses clip layered above at the matched moment, playing its
     own sound,
   - each clip freely trimmable/extendable — sync is not lost when you
     drag its edges, because the clip's media is already aligned.

## Updates

When a newer version exists, a small prompt appears in the bottom-left
corner at launch: **Update now** downloads it, replaces the app, and
reopens it. Dismiss with ✕ to keep working; it reappears next launch
until you update.

The repository is public, so the update check works for everyone with
no setup. (If it is ever made private again, set `EDITSYNC_GITHUB_TOKEN`
to a read-access token on each machine; without access the app simply
never shows the prompt.)

## Good to know

- **The app remembers everything** — project name, title card text and
  style, framing, blur, audio behavior, music settings, formats, and
  window size are all restored on next launch.

- **Green markers** on clips say how confident the match was. A **red
  marker** means "double-check this one by ear."
- If a clip couldn't be matched confidently, the app **leaves it out and
  tells you**, rather than guessing wrong. You'll see it in the results
  screen.
- Keep your footage where it was when you synced — the project file
  points at the videos, it doesn't copy them. If you move them, Final Cut
  will ask you to relink.
- The app never changes or writes over your footage. It only reads it.
