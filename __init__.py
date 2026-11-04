# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamepad Navigation: fly Blender's 3D viewport with a game controller.

Sidebar (N) > View > Gamepad > Start. Moves the largest 3D viewport's view, or the scene
camera when that viewport is looking through it.

  left stick      move along where the view looks (forward/back, strafe)
  right stick     look (turn about world up, tilt)
  left bumper     down             right bumper   up
  right trigger   sprint, progressive (up to x4)
  left trigger    crawl, progressive (down to x0.2)

Controllers are read through the SDL3 that ships with Blender (see sdl.py), so
PlayStation, Xbox, Switch and most other pads work without extra drivers.
"""

import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import sdl as _sdl_module

RATE = 1 / 60
DEAD_STICK, DEAD_TRIGGER = 0.15, 0.05
SPRINT, CRAWL = 4.0, 0.2

_sdl = None          # the loaded SDL3, on first Start
_pad = None          # the open gamepad
_pad_name = ""
_running = False
_last = 0.0
_next_scan = 0.0


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# --- controller --------------------------------------------------------------------

def _open_first_pad():
    global _pad, _pad_name
    for jid in _sdl.gamepad_ids():
        pad = _sdl.SDL_OpenGamepad(jid)
        if pad:
            name = _sdl.SDL_GetGamepadName(pad)
            _pad, _pad_name = pad, name.decode(errors="replace") if name else "Gamepad"
            return


def _close_pad():
    global _pad, _pad_name
    if _pad and _sdl:
        _sdl.SDL_CloseGamepad(_pad)
    _pad, _pad_name = None, ""


def _stick(raw):
    return max(-1.0, min(1.0, raw / _sdl_module.AXIS_MAX))


def _shape(x, y):
    """Radial dead zone, rescaled so motion starts from zero, then a squared response."""
    m = math.hypot(x, y)
    if m < DEAD_STICK:
        return 0.0, 0.0
    k = (min(m, 1.0) - DEAD_STICK) / (1 - DEAD_STICK)
    k *= k
    return x / m * k, y / m * k


def _trigger(raw):
    t = max(0.0, raw / _sdl_module.AXIS_MAX)
    return 0.0 if t < DEAD_TRIGGER else (t - DEAD_TRIGGER) / (1 - DEAD_TRIGGER)


# --- view --------------------------------------------------------------------------

def _target_view():
    """The largest 3D viewport in the first window: (area, region_3d)."""
    best = None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D' and (best is None or area.width * area.height > best.width * best.height):
                best = area
        break
    return (best, best.spaces.active.region_3d) if best else (None, None)


def _turn(rot, yaw, pitch):
    """Yaw about world up, pitch about the view's own right axis; no flipping over the top."""
    right = rot @ Vector((1, 0, 0))
    q = Quaternion((0, 0, 1), yaw) @ Quaternion(right, pitch) @ rot
    if abs((q @ Vector((0, 0, -1))).z) > 0.98:   # nearly straight up or down: keep the yaw only
        q = Quaternion((0, 0, 1), yaw) @ rot
    return q


def _tick():
    global _last, _next_scan
    if not _running:
        return None
    s = _sdl
    now = time.perf_counter()
    dt = min(now - _last, 0.1)
    _last = now
    s.SDL_UpdateGamepads()                      # also notices controllers being plugged in
    if _pad is None or not s.SDL_GamepadConnected(_pad):
        _close_pad()
        if now >= _next_scan:                   # look for a controller once a second
            _next_scan = now + 1.0
            _open_first_pad()
        return RATE

    lx, ly = _shape(_stick(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_LEFTX)),
                    _stick(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_LEFTY)))
    rx, ry = _shape(_stick(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_RIGHTX)),
                    _stick(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_RIGHTY)))
    lt = _trigger(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_LEFT_TRIGGER))
    rt = _trigger(s.SDL_GetGamepadAxis(_pad, _sdl_module.AXIS_RIGHT_TRIGGER))
    rise = (float(s.SDL_GetGamepadButton(_pad, _sdl_module.BUTTON_RIGHT_SHOULDER))
            - float(s.SDL_GetGamepadButton(_pad, _sdl_module.BUTTON_LEFT_SHOULDER)))
    if not (lx or ly or rx or ry or rise):
        return RATE                             # nothing pressed: write nothing

    prefs = _prefs()
    speed = prefs.speed * (1 + (SPRINT - 1) * rt) * (1 + (CRAWL - 1) * lt)
    look = math.radians(prefs.look_speed)
    yaw = -rx * look * dt
    pitch = (ry if prefs.invert_look else -ry) * look * dt

    area, rv3d = _target_view()
    if rv3d is None:
        return RATE
    cam = bpy.context.scene.camera if rv3d.view_perspective == 'CAMERA' else None
    if cam is not None:
        loc, rot, scale = cam.matrix_world.decompose()
    else:
        if rv3d.view_perspective == 'ORTHO':
            rv3d.view_perspective = 'PERSP'     # flying in a flat view would show no movement
        rot = rv3d.view_rotation.copy()
        loc = rv3d.view_location + rot @ Vector((0, 0, rv3d.view_distance))   # the eye
    rot = _turn(rot, yaw, pitch)
    move = (rot @ Vector((1, 0, 0))) * lx + (rot @ Vector((0, 0, -1))) * -ly + Vector((0, 0, 1)) * rise
    loc = loc + move * speed * dt
    if cam is not None:
        cam.matrix_world = Matrix.LocRotScale(loc, rot, scale)
    else:
        rv3d.view_rotation = rot
        rv3d.view_location = loc - rot @ Vector((0, 0, rv3d.view_distance))
    area.tag_redraw()
    return RATE


# --- start / stop ------------------------------------------------------------------

def _start():
    global _sdl, _running, _last, _next_scan
    if _sdl is None:
        _sdl = _sdl_module.SDL(bpy.app.binary_path)
    _sdl.SDL_SetHint(_sdl_module.HINT_BACKGROUND_EVENTS, b"1")   # Blender has the focus, not SDL
    if not _sdl.SDL_Init(_sdl_module.INIT_GAMEPAD):
        raise RuntimeError("SDL_Init failed: " + _sdl.error())
    # State is read with SDL_UpdateGamepads; with no event loop, queued events would only pile up.
    _sdl.SDL_SetGamepadEventsEnabled(False)
    _sdl.SDL_SetJoystickEventsEnabled(False)
    _running, _last, _next_scan = True, time.perf_counter(), 0.0
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=RATE)


def _stop():
    global _running
    _running = False
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _close_pad()
    if _sdl:
        _sdl.SDL_QuitSubSystem(_sdl_module.INIT_GAMEPAD)


class GAMEPADNAV_OT_toggle(bpy.types.Operator):
    """Start or stop flying the viewport with a game controller"""
    bl_idname = "gamepad_nav.toggle"
    bl_label = "Gamepad Navigation"

    def execute(self, context):
        if _running:
            _stop()
            return {'FINISHED'}
        try:
            _start()
        except (OSError, RuntimeError, AttributeError) as e:
            self.report({'ERROR'}, "Gamepad Navigation: %s" % e)
            return {'CANCELLED'}
        return {'FINISHED'}


# --- UI ----------------------------------------------------------------------------

class GAMEPADNAV_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    speed: bpy.props.FloatProperty(name="Speed (m/s)", default=1.5, min=0.05, max=50.0,
                                   description="Flying speed with the stick pushed all the way")
    look_speed: bpy.props.FloatProperty(name="Look (°/s)", default=120.0, min=10.0, max=720.0,
                                        description="Turning speed with the right stick pushed all the way")
    invert_look: bpy.props.BoolProperty(name="Invert look up/down", default=False)
    auto_start: bpy.props.BoolProperty(name="Start automatically", default=False,
                                       description="Start listening for a controller when Blender starts")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "speed")
        col.prop(self, "look_speed")
        col.prop(self, "invert_look")
        col.prop(self, "auto_start")


class GAMEPADNAV_PT_panel(bpy.types.Panel):
    bl_label = "Gamepad"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context):
        layout = self.layout
        prefs = _prefs()
        layout.operator("gamepad_nav.toggle", text="Stop" if _running else "Start",
                        icon='PAUSE' if _running else 'PLAY', depress=_running)
        if _running:
            layout.label(text=_pad_name or "No controller found", icon='CHECKMARK' if _pad else 'ERROR')
        col = layout.column(align=True)
        col.prop(prefs, "speed")
        col.prop(prefs, "look_speed")
        layout.prop(prefs, "invert_look")


_classes = (GAMEPADNAV_OT_toggle, GAMEPADNAV_Preferences, GAMEPADNAV_PT_panel)


def _auto_start():
    try:
        if _prefs().auto_start and not _running:
            _start()
    except (OSError, RuntimeError, AttributeError, KeyError) as e:
        print("Gamepad Navigation: auto-start failed:", e)
    return None


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.app.timers.register(_auto_start, first_interval=1.0)


def unregister():
    _stop()
    if bpy.app.timers.is_registered(_auto_start):
        bpy.app.timers.unregister(_auto_start)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
