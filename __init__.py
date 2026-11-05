# SPDX-License-Identifier: MIT
"""Gamepad Navigation: fly Blender's 3D viewport with a game controller.

Sidebar (N) > View > Gamepad > Start. Moves the largest 3D viewport's view, or the scene
camera when that viewport is looking through it.

  left stick      move along where the view looks (forward/back, strafe); in an
                  orthographic view forward/back zooms instead
  right stick     look (turn about world up, tilt)
  left bumper     down             right bumper   up
  right trigger   sprint, progressive        left trigger   crawl, progressive
  face buttons, D-pad, thumbstick buttons, Special Left / Right
                  any Blender action (operator), set in the add-on's preferences;
                  Face Button North (Triangle / Y) toggles perspective / orthographic

Controllers are read through the SDL3 that ships with Blender (see sdl.py), so
PlayStation, Xbox, Switch and most other pads work without extra drivers.
"""

import ast
import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import sdl as S

RATE = 1 / 60
DEAD_TRIGGER = 0.05

_sdl = None          # the loaded SDL3, on first Start
_pad = None          # the open gamepad
_pad_name = ""
_running = False
_last = 0.0
_next_scan = 0.0
_held = set()        # mappable buttons down on the last tick, so an action fires once per press
_last_error = ""


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# --- button mappings ---------------------------------------------------------------

# (preference key, label, SDL button, what it's called on DualSense / Xbox). Face buttons
# are named by position, which SDL keeps the same on every pad.
BUTTONS = (
    ("north", "Face Button North", S.BUTTON_NORTH, "DualSense: Triangle  ·  Xbox: Y"),
    ("south", "Face Button South", S.BUTTON_SOUTH, "DualSense: Cross  ·  Xbox: A"),
    ("east", "Face Button East", S.BUTTON_EAST, "DualSense: Circle  ·  Xbox: B"),
    ("west", "Face Button West", S.BUTTON_WEST, "DualSense: Square  ·  Xbox: X"),
    ("dpad_up", "D-Pad Up", S.BUTTON_DPAD_UP, "DualSense and Xbox: D-pad up"),
    ("dpad_down", "D-Pad Down", S.BUTTON_DPAD_DOWN, "DualSense and Xbox: D-pad down"),
    ("dpad_left", "D-Pad Left", S.BUTTON_DPAD_LEFT, "DualSense and Xbox: D-pad left"),
    ("dpad_right", "D-Pad Right", S.BUTTON_DPAD_RIGHT, "DualSense and Xbox: D-pad right"),
    ("left_stick", "Left Thumbstick Button", S.BUTTON_LEFT_STICK,
     "Pressing the left stick in.  DualSense: L3  ·  Xbox: LS"),
    ("right_stick", "Right Thumbstick Button", S.BUTTON_RIGHT_STICK,
     "Pressing the right stick in.  DualSense: R3  ·  Xbox: RS"),
    ("back", "Special Left", S.BUTTON_BACK,
     "DualSense: Create (Share on DualShock 4)  ·  Xbox: View (Back on Xbox 360)"),
    ("start", "Special Right", S.BUTTON_START,
     "DualSense: Options  ·  Xbox: Menu (Start on Xbox 360)"),
)
DEFAULTS = {"north": 'PERSP_ORTHO'}

# Ready-made actions: (enum id, label, operator call). 'CUSTOM' uses the typed operator.
ACTIONS = (
    ('NONE', "Nothing", ""),
    ('PERSP_ORTHO', "Toggle perspective / orthographic", "view3d.view_persportho"),
    ('CAMERA', "Look through camera / back", "view3d.view_camera"),
    ('FRAME_SELECTED', "Frame selected", "view3d.view_selected"),
    ('FRAME_ALL', "Frame all", "view3d.view_all"),
    ('VIEW_FRONT', "Front view", "view3d.view_axis(type='FRONT')"),
    ('VIEW_RIGHT', "Right view", "view3d.view_axis(type='RIGHT')"),
    ('VIEW_TOP', "Top view", "view3d.view_axis(type='TOP')"),
    ('XRAY', "Toggle X-ray", "view3d.toggle_xray"),
    ('PLAY', "Play / pause animation", "screen.animation_play"),
    ('UNDO', "Undo", "ed.undo"),
    ('REDO', "Redo", "ed.redo"),
    ('CUSTOM', "Custom operator…", ""),
)
_ACTION_CALL = {a[0]: a[2] for a in ACTIONS}


def _parse_call(text):
    """'view3d.view_axis(type="TOP")' or 'view3d.view_all' -> (('view3d', 'view_axis'), {...})."""
    node = ast.parse(text.strip(), mode="eval").body
    kwargs = {}
    if isinstance(node, ast.Call):
        if node.args:
            raise ValueError("use keyword arguments, e.g. type='TOP'")
        kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or len(parts) != 1:
        raise ValueError("expected an operator like view3d.view_all")
    return (node.id, parts[0]), kwargs


def _run_action(key, area):
    """Run the action mapped to button `key`, in the 3D viewport being flown."""
    global _last_error
    prefs = _prefs()
    action = getattr(prefs, "btn_" + key)
    call = getattr(prefs, "btn_" + key + "_custom") if action == 'CUSTOM' else _ACTION_CALL.get(action, "")
    if not call:
        return
    try:
        (module, name), kwargs = _parse_call(call)
        op = getattr(getattr(bpy.ops, module), name)
        win = bpy.context.window_manager.windows[0]
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
            op(**kwargs)
        _last_error = ""
    except Exception as e:                      # a bad mapping must not stop navigation
        _last_error = "%s: %s" % (call, e)
        print("Gamepad Navigation:", _last_error)


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
    _held.clear()


def _stick(raw):
    return max(-1.0, min(1.0, raw / S.AXIS_MAX))


def _shape(x, y, dead):
    """Radial dead zone, rescaled so motion starts from zero, then a squared response."""
    m = math.hypot(x, y)
    if m < dead:
        return 0.0, 0.0
    k = (min(m, 1.0) - dead) / (1 - dead)
    k *= k
    return x / m * k, y / m * k


def _trigger(raw):
    t = max(0.0, raw / S.AXIS_MAX)
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


def _fly(rv3d, lx, ly, rx, ry, rise, speed, dt, prefs):
    look = math.radians(prefs.look_speed)
    yaw = -rx * look * dt
    pitch = (ry if prefs.invert_look else -ry) * look * dt
    cam = bpy.context.scene.camera if rv3d.view_perspective == 'CAMERA' else None
    if cam is not None:
        loc, rot, scale = cam.matrix_world.decompose()
        rot = _turn(rot, yaw, pitch)
        move = (rot @ Vector((1, 0, 0))) * lx + (rot @ Vector((0, 0, -1))) * -ly + Vector((0, 0, 1)) * rise
        cam.matrix_world = Matrix.LocRotScale(loc + move * speed * dt, rot, scale)
        return
    rot = rv3d.view_rotation.copy()
    eye = rv3d.view_location + rot @ Vector((0, 0, rv3d.view_distance))
    rot = _turn(rot, yaw, pitch)
    if rv3d.view_perspective == 'ORTHO':
        # A flat view shows no forward motion: forward/back zooms, the rest pans.
        rv3d.view_rotation = rot
        pan = (rot @ Vector((1, 0, 0))) * lx + Vector((0, 0, 1)) * rise
        rv3d.view_location = eye - rot @ Vector((0, 0, rv3d.view_distance)) + pan * speed * dt
        rv3d.view_distance = max(0.05, rv3d.view_distance + ly * speed * dt)
        return
    move = (rot @ Vector((1, 0, 0))) * lx + (rot @ Vector((0, 0, -1))) * -ly + Vector((0, 0, 1)) * rise
    eye = eye + move * speed * dt
    rv3d.view_rotation = rot
    rv3d.view_location = eye - rot @ Vector((0, 0, rv3d.view_distance))


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

    prefs = _prefs()
    area, rv3d = _target_view()
    if rv3d is None:
        return RATE

    # Mapped buttons fire once, on the press.
    for key, _, button, _ in BUTTONS:
        if s.SDL_GetGamepadButton(_pad, button):
            if key not in _held:
                _held.add(key)
                _run_action(key, area)
        else:
            _held.discard(key)

    lx, ly = _shape(_stick(s.SDL_GetGamepadAxis(_pad, S.AXIS_LEFTX)),
                    _stick(s.SDL_GetGamepadAxis(_pad, S.AXIS_LEFTY)), prefs.dead_zone)
    rx, ry = _shape(_stick(s.SDL_GetGamepadAxis(_pad, S.AXIS_RIGHTX)),
                    _stick(s.SDL_GetGamepadAxis(_pad, S.AXIS_RIGHTY)), prefs.dead_zone)
    rise = (float(s.SDL_GetGamepadButton(_pad, S.BUTTON_RIGHT_SHOULDER))
            - float(s.SDL_GetGamepadButton(_pad, S.BUTTON_LEFT_SHOULDER)))
    if lx or ly or rx or ry or rise:
        lt = _trigger(s.SDL_GetGamepadAxis(_pad, S.AXIS_LEFT_TRIGGER))
        rt = _trigger(s.SDL_GetGamepadAxis(_pad, S.AXIS_RIGHT_TRIGGER))
        speed = prefs.speed * (1 + (prefs.sprint - 1) * rt) * (1 + (prefs.crawl - 1) * lt)
        _fly(rv3d, lx, ly, rx, ry, rise, speed, dt, prefs)
        area.tag_redraw()
    return RATE


# --- start / stop ------------------------------------------------------------------

def _start():
    global _sdl, _running, _last, _next_scan
    if _sdl is None:
        _sdl = S.SDL(bpy.app.binary_path)
    _sdl.SDL_SetHint(S.HINT_BACKGROUND_EVENTS, b"1")   # Blender has the focus, not SDL
    if not _sdl.SDL_Init(S.INIT_GAMEPAD):
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
        _sdl.SDL_QuitSubSystem(S.INIT_GAMEPAD)


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


# --- settings ----------------------------------------------------------------------

class GAMEPADNAV_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    speed: bpy.props.FloatProperty(name="Move speed (m/s)", default=3.0, min=0.05, max=100.0,
                                   description="Flying speed with the stick pushed all the way")
    look_speed: bpy.props.FloatProperty(name="Look speed (°/s)", default=120.0, min=10.0, max=720.0,
                                        description="Turning speed with the right stick pushed all the way")
    invert_look: bpy.props.BoolProperty(name="Invert look up/down", default=False,
                                        description="Push the right stick up to look down")
    sprint: bpy.props.FloatProperty(name="Sprint (right trigger) ×", default=4.0, min=1.0, max=20.0,
                                    description="Speed multiplier with the right trigger fully pressed")
    crawl: bpy.props.FloatProperty(name="Crawl (left trigger) ×", default=0.2, min=0.01, max=1.0,
                                   description="Speed multiplier with the left trigger fully pressed")
    dead_zone: bpy.props.FloatProperty(name="Stick dead zone", default=0.15, min=0.0, max=0.5,
                                       subtype='FACTOR',
                                       description="How far a stick must move before it counts; raise it if the view drifts")
    auto_start: bpy.props.BoolProperty(name="Start automatically", default=False,
                                       description="Start listening for a controller when Blender starts")

    def draw(self, context):
        layout = self.layout
        col = layout.column(heading="Movement")
        col.prop(self, "speed")
        col.prop(self, "look_speed")
        col.prop(self, "invert_look")
        col.prop(self, "sprint")
        col.prop(self, "crawl")
        col.prop(self, "dead_zone")
        col.prop(self, "auto_start")
        layout.separator()
        layout.label(text="Buttons")
        _draw_buttons(layout, self)


def _draw_buttons(layout, prefs):
    col = layout.column(align=True)
    for key, label, _, _ in BUTTONS:
        row = col.row(align=True)
        row.label(text=label)
        row.prop(prefs, "btn_" + key, text="")
        if getattr(prefs, "btn_" + key) == 'CUSTOM':
            col.prop(prefs, "btn_" + key + "_custom", text="", icon='CONSOLE')
    if _last_error:
        layout.label(text=_last_error, icon='ERROR')


# One action and one custom-operator field per mappable button. The description is the
# dropdown's tooltip: what the button is called on the common controllers.
for _key, _label, _, _names in BUTTONS:
    GAMEPADNAV_Preferences.__annotations__["btn_" + _key] = bpy.props.EnumProperty(
        name=_label, description=_names,
        items=[(a[0], a[1], a[2]) for a in ACTIONS], default=DEFAULTS.get(_key, 'NONE'))
    GAMEPADNAV_Preferences.__annotations__["btn_" + _key + "_custom"] = bpy.props.StringProperty(
        name=_label + " operator", description=_names + ".  Any operator, e.g. view3d.view_axis(type='TOP')")


# --- panels ------------------------------------------------------------------------

class GAMEPADNAV_PT_panel(bpy.types.Panel):
    bl_label = "Gamepad"
    bl_idname = "GAMEPADNAV_PT_panel"
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
        col.prop(prefs, "speed", text="Move")
        col.prop(prefs, "look_speed", text="Look")
        col = layout.column(align=True)
        col.prop(prefs, "sprint", text="Sprint ×")
        col.prop(prefs, "crawl", text="Crawl ×")
        layout.prop(prefs, "invert_look")


class GAMEPADNAV_PT_buttons(bpy.types.Panel):
    bl_label = "Buttons"
    bl_parent_id = "GAMEPADNAV_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        _draw_buttons(self.layout, _prefs())


_classes = (GAMEPADNAV_OT_toggle, GAMEPADNAV_Preferences, GAMEPADNAV_PT_panel, GAMEPADNAV_PT_buttons)


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
