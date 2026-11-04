# Gamepad Navigation for Blender

Fly Blender's 3D viewport with a game controller: walk through a scene the way you would
in a game editor, or drive the camera you're looking through.

Controllers are read through [SDL3](https://libsdl.org), so PlayStation, Xbox, Switch and
most other pads work with no extra drivers or mapping tools.

## Controls

| Input | Does |
| --- | --- |
| Left stick | move: forward/back along where you're looking, strafe left/right |
| Right stick | look around |
| Left bumper / right bumper | down / up |
| Right trigger | sprint, progressive (up to 4×) |
| Left trigger | crawl, progressive (down to 0.2×) |

Other buttons are not mapped yet.

It moves the largest 3D viewport. If that viewport is looking through the scene camera
(numpad `0`), it moves **the camera** instead. A flat front/side/top view switches to
perspective as soon as you move.

## Use

**Sidebar (`N`) → View → Gamepad → Start.** The panel shows the controller it found, the
flying speed (m/s) and look speed (°/s), and *Invert look up/down*. The same settings,
plus *Start automatically*, are in the add-on's preferences.

The stick dead zone is 15%, so a pad that drifts a little at rest doesn't move the view.

## Install

Requires Blender 4.2 or newer. Windows x64 for now.

1. Download the `.zip` from the [releases](https://github.com/starfrogdev/blender-gamepad-nav/releases).
2. Blender: **Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…**,
   pick the zip.

## Build from source

```bash
python tools/fetch_sdl.py
mkdir dist
blender --command extension build --output-dir dist
```

`fetch_sdl.py` downloads the official SDL3 release for Windows x64, checks its SHA-256,
and puts `SDL3.dll` in `lib/`. `extension build` makes the installable zip in `dist/`.

On Linux and macOS, install SDL3 from your package manager. The add-on looks for a system
SDL3 when `lib/` has none. The manifest only lists Windows for now, so you'll need to
add your platform to it to build.

## Licence

GPL-3.0-or-later (see `LICENSE`), like Blender add-ons in general. Release builds bundle
SDL3, which is under the zlib licence (`lib/LICENSE-SDL3.txt`).
