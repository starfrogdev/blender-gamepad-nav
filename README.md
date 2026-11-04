# Gamepad Navigation for Blender

Fly Blender's 3D viewport with a game controller: walk through a scene the way you would
in a game editor, or drive the camera you're looking through.

Controllers are read through [SDL3](https://libsdl.org), the copy that already ships
with Blender, so PlayStation, Xbox, Switch and most other pads work with no extra drivers,
mapping tools or downloads.

## Controls

| Input | Does |
| --- | --- |
| Left stick | move: forward/back along where you're looking, strafe left/right |
| Right stick | look around |
| Left bumper / right bumper | down / up |
| Right trigger | sprint, progressive (default up to 4×) |
| Left trigger | crawl, progressive (default down to 0.2×) |
| Face Button North (Triangle / Y) | toggle perspective / orthographic (default mapping) |

In an **orthographic** view the left stick's forward/back **zooms** (a flat view shows no
forward motion); strafing and up/down pan.

If the viewport is looking through the scene camera (numpad `0`), the gamepad moves
**the camera** instead of the view. It always drives the largest 3D viewport.

## Use

**Sidebar (`N`) → View → Gamepad → Start.** The panel shows the controller it found and
the main speeds; its **Buttons** sub-panel holds the button mappings. Everything is also in
the add-on's preferences.

### Settings

| Setting | Default | |
| --- | --- | --- |
| Move speed | 3 m/s | stick pushed all the way |
| Look speed | 120 °/s | right stick pushed all the way |
| Invert look up/down | off | |
| Sprint (right trigger) × | 4 | speed multiplier at full trigger |
| Crawl (left trigger) × | 0.2 | speed multiplier at full trigger |
| Stick dead zone | 0.15 | raise it if the view drifts on its own |
| Start automatically | off | listen for a controller from Blender's start |

### Button mappings

Twelve buttons can each run any Blender action. They're named
by position rather than by symbol, so the names hold on every controller; hover a
button's dropdown to see what it's called on DualSense and Xbox:

| Button | DualSense | Xbox |
| --- | --- | --- |
| Face Button North | Triangle | Y |
| Face Button South | Cross | A |
| Face Button East | Circle | B |
| Face Button West | Square | X |
| D-Pad Up / Down / Left / Right | D-pad | D-pad |
| Left Thumbstick Button | L3 | LS |
| Right Thumbstick Button | R3 | RS |
| Special Left | Create (Share on DualShock 4) | View (Back on Xbox 360) |
| Special Right | Options | Menu (Start on Xbox 360) |

Pick a ready-made action (toggle perspective/orthographic, look through camera, frame
selected / all, front / right / top view, toggle X-ray, play / pause, undo, redo) or
**Custom operator…** and type any operator, with keyword arguments if it takes them:
`view3d.view_axis(type='TOP')`, `screen.frame_jump(end=False)`. That's what a Blender
keybinding runs, so anything you can bind to a key works here. An action fires once per
press. A mapping that fails shows its error in the Buttons panel; navigation keeps going.

Only Face Button North is mapped by default; the rest do nothing until you set them.

## Install

Requires Blender 5.2 or newer (the first version it has been checked with; Blender ships
SDL3, which this uses). Tested on Windows; Linux and macOS should work the same way, and a
system-installed SDL3 is used if Blender's own can't be found.

1. Download the `.zip` from the [releases](https://github.com/starfrogdev/blender-gamepad-nav/releases).
2. Blender: **Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…**,
   pick the zip.

## Build from source

```bash
mkdir dist
blender --command extension build --output-dir dist
```

That makes the installable zip in `dist/`. There are no dependencies to fetch: the
extension is plain Python, and it binds SDL3 with `ctypes` (`sdl.py`).

## Licence

GPL-3.0-or-later (see `LICENSE`), like Blender add-ons in general.
