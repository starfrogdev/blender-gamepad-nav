# SPDX-License-Identifier: MIT

"""Game controller navigation for Blender's 3D viewport.

Controllers are read through the SDL3 that ships with Blender.
"""

import ast
import configparser
import ctypes
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import sdl as S

# Tuning values and user settings, kept in settings.ini next to this file.
# SEE: settings.ini
_CONFIG = configparser.ConfigParser(interpolation=None)
_CONFIG.optionxform = str
if not _CONFIG.read(os.path.join(os.path.dirname(__file__), "settings.ini"), encoding="utf-8"):
    raise RuntimeError("Cannot load Gamepad Navigation without settings.ini next to __init__.py")
_TUNING = _CONFIG["tuning"]

RATE = 1 / _TUNING.getfloat("poll_hz")
MAX_STEP = _TUNING.getfloat("max_step")
RESCAN_INTERVAL = _TUNING.getfloat("rescan_interval")
DEAD_TRIGGER = _TUNING.getfloat("dead_trigger")
PITCH_LIMIT = _TUNING.getfloat("pitch_limit")
MIN_ORTHO_DISTANCE = _TUNING.getfloat("min_ortho_distance")

_sdl = None          # SDL3 bindings, loaded on the first Start
_pad = None          # gamepad being read, or None while none is connected
_pad_name = ""
_running = False
_last = 0.0
_held = set()        # mapped buttons held on the last tick, for firing each action once per press
_last_error = ""


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# --- button mappings ---------------------------------------------------------------

# Mappable buttons as (preference key, label, SDL button, DualSense and Xbox names). Labels
# name a button by its position rather than its symbol because SDL keeps positions the same
# on every pad.
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
DEFAULTS = {"north": 'PERSP_ORTHO'}     # buttons mapped out of the box

# Ready-made actions offered for each button, as (enum id, label, operator call). `CUSTOM`
# runs the operator typed into the button's custom field.
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
    """Splits an operator call like `view3d.view_axis(type='TOP')` into its `bpy.ops` path and
    keyword arguments, as `(('view3d', 'view_axis'), {'type': 'TOP'})`."""
    node = ast.parse(text.strip(), mode="eval").body
    kwargs = {}
    if isinstance(node, ast.Call):
        if node.args:
            raise ValueError("Cannot pass positional arguments to an operator. Use keywords like type='TOP'")
        kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or len(parts) != 1:
        raise ValueError("Cannot read this as an operator. Write it like view3d.view_all")
    return (node.id, parts[0]), kwargs


def _run_action(key, area):
    """Runs the action mapped to button `key` in the 3D viewport being flown."""
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
    except Exception as e:                      # keeps navigation running when a mapping fails
        _last_error = "Could not run %s (%s)" % (call, e)
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
    """Shapes one stick's reading into flying input with a radial dead zone and a squared
    response. NOTE: The output is rescaled to start from zero at the dead zone's edge."""
    m = math.hypot(x, y)
    if m < dead:
        return 0.0, 0.0
    k = (min(m, 1.0) - dead) / (1 - dead)
    k *= k
    return x / m * k, y / m * k


def _trigger(raw):
    t = max(0.0, raw / S.AXIS_MAX)
    return 0.0 if t < DEAD_TRIGGER else (t - DEAD_TRIGGER) / (1 - DEAD_TRIGGER)


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32")
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _user32.GetWindowThreadProcessId.restype = ctypes.c_ulong


def _blender_has_focus():
    """Returns whether one of Blender's windows is the app in front. Always True off Windows,
    where there is no check yet."""
    if sys.platform != "win32":
        return True
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = ctypes.c_ulong(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


# --- view --------------------------------------------------------------------------

def _target_view():
    """Returns the largest 3D viewport in the first window as `(area, region_3d)`, or
    `(None, None)` when there is none."""
    best = None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D' and (best is None or area.width * area.height > best.width * best.height):
                best = area
        break
    return (best, best.spaces.active.region_3d) if best else (None, None)


def _turn(rot, yaw, pitch):
    """Turns view rotation `rot` by `yaw` about world up and `pitch` about its own right axis
    without flipping over the top."""
    right = rot @ Vector((1, 0, 0))
    q = Quaternion((0, 0, 1), yaw) @ Quaternion(right, pitch) @ rot
    if abs((q @ Vector((0, 0, -1))).z) > PITCH_LIMIT:   # drops the pitch when the view nears straight up or down
        q = Quaternion((0, 0, 1), yaw) @ rot
    return q


def _move_vector(rot, lx, ly, rise):
    """Returns the direction to fly for one tick of stick input, before speed and time."""
    return (rot @ Vector((1, 0, 0))) * lx + (rot @ Vector((0, 0, -1))) * -ly + Vector((0, 0, 1)) * rise


def _fly(rv3d, lx, ly, rx, ry, rise, speed, dt, prefs):
    """Moves and turns the view in `rv3d`, or the scene camera it looks through, by one tick
    of stick input."""
    look = math.radians(prefs.look_speed)
    yaw = -rx * look * dt
    pitch = (ry if prefs.invert_look else -ry) * look * dt
    cam = bpy.context.scene.camera if rv3d.view_perspective == 'CAMERA' else None
    if cam is not None:
        loc, rot, scale = cam.matrix_world.decompose()
        rot = _turn(rot, yaw, pitch)
        move = _move_vector(rot, lx, ly, rise)
        cam.matrix_world = Matrix.LocRotScale(loc + move * speed * dt, rot, scale)
        return
    rot = rv3d.view_rotation.copy()
    eye = rv3d.view_location + rot @ Vector((0, 0, rv3d.view_distance))
    rot = _turn(rot, yaw, pitch)
    if rv3d.view_perspective == 'ORTHO':
        # Zooms on forward/back and pans on the rest because a flat view shows no forward motion.
        rv3d.view_rotation = rot
        pan = (rot @ Vector((1, 0, 0))) * lx + Vector((0, 0, 1)) * rise
        rv3d.view_location = eye - rot @ Vector((0, 0, rv3d.view_distance)) + pan * speed * dt
        rv3d.view_distance = max(MIN_ORTHO_DISTANCE, rv3d.view_distance + ly * speed * dt)
        return
    eye = eye + _move_vector(rot, lx, ly, rise) * speed * dt
    rv3d.view_rotation = rot
    rv3d.view_location = eye - rot @ Vector((0, 0, rv3d.view_distance))


def _tick():
    """Polls the controller and flies the view once. Returns the timer's next interval, which
    is `RATE` while a controller is connected and `RESCAN_INTERVAL` while none is, or None to
    stop it."""
    global _last
    if not _running:
        return None
    s = _sdl
    now = time.perf_counter()
    dt = min(now - _last, MAX_STEP)
    _last = now
    s.SDL_UpdateGamepads()                      # reads the pads and picks up newly plugged-in ones
    if _pad is None or not s.SDL_GamepadConnected(_pad):
        _close_pad()
        _open_first_pad()
        return RATE if _pad else RESCAN_INTERVAL

    prefs = _prefs()
    area, rv3d = _target_view()
    if rv3d is None:
        return RATE

    if prefs.only_when_focused and not _blender_has_focus():
        # Marks the buttons held now as already pressed. They don't fire on switching back.
        _held.update(key for key, _, button, _ in BUTTONS if s.SDL_GetGamepadButton(_pad, button))
        return RATE

    # Fires each mapped action once per press.
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
    global _sdl, _running, _last
    if _sdl is None:
        _sdl = S.SDL(bpy.app.binary_path)
    _sdl.SDL_SetHint(S.HINT_BACKGROUND_EVENTS, b"1")   # lets SDL read the pad while Blender holds the focus
    if not _sdl.SDL_Init(S.INIT_GAMEPAD):
        raise RuntimeError("Cannot open the controller subsystem because SDL_Init failed: " + _sdl.error())
    # Turns off SDL's event queue because nothing drains it. `_tick` reads state with
    # `SDL_UpdateGamepads` instead.
    _sdl.SDL_SetGamepadEventsEnabled(False)
    _sdl.SDL_SetJoystickEventsEnabled(False)
    _running, _last = True, time.perf_counter()
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
            self.report({'ERROR'}, "Cannot start Gamepad Navigation. %s" % e)
            return {'CANCELLED'}
        return {'FINISHED'}


# --- settings ----------------------------------------------------------------------

class GAMEPADNAV_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    __annotations__ = {}         # filled from settings.ini and BUTTONS below

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
        col.prop(self, "only_when_focused")
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


def _setting_property(section):
    """Builds the Blender property that one `[setting.<name>]` section of settings.ini describes."""
    kind = section["type"]
    kwargs = {"name": section["name"], "description": section.get("description", "")}
    if kind == "bool":
        return bpy.props.BoolProperty(default=section.getboolean("default"), **kwargs)
    if kind == "float":
        for key in ("min", "max"):
            if key in section:
                kwargs[key] = section.getfloat(key)
        if "subtype" in section:
            kwargs["subtype"] = section["subtype"]
        return bpy.props.FloatProperty(default=section.getfloat("default"), **kwargs)
    raise ValueError("Cannot build setting [%s] of type %r. Use float or bool" % (section.name, kind))


# Adds one preference for each [setting.<name>] section, in the file's order.
for _section in _CONFIG.sections():
    if _section.startswith("setting."):
        GAMEPADNAV_Preferences.__annotations__[_section[len("setting."):]] = _setting_property(_CONFIG[_section])

# Adds an action and a custom-operator field for each mappable button. Each dropdown's
# tooltip names the button on DualSense and Xbox.
for _key, _label, _, _names in BUTTONS:
    GAMEPADNAV_Preferences.__annotations__["btn_" + _key] = bpy.props.EnumProperty(
        name=_label, description=_names,
        items=[(a[0], a[1], a[2]) for a in ACTIONS], default=DEFAULTS.get(_key, 'NONE'))
    GAMEPADNAV_Preferences.__annotations__["btn_" + _key + "_custom"] = bpy.props.StringProperty(
        name=_label + " operator", description=_names + ".  Any operator, such as view3d.view_axis(type='TOP')")


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
        layout.prop(prefs, "only_when_focused")


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
        print("Gamepad Navigation could not start automatically.", e)
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
