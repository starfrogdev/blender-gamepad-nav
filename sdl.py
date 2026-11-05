# SPDX-License-Identifier: MIT

"""The few SDL3 gamepad functions this extension uses, bound with ctypes.

Loads the SDL3 that ships with Blender, such as blender.shared/SDL3.dll next to blender.exe
on Windows. A system-installed SDL3 is the fallback.
"""

import ctypes
import ctypes.util
import glob
import os
import sys

INIT_GAMEPAD = 0x00002000

# SEE: SDL_GamepadAxis and SDL_GamepadButton in SDL3's SDL_gamepad.h
AXIS_LEFTX, AXIS_LEFTY, AXIS_RIGHTX, AXIS_RIGHTY, AXIS_LEFT_TRIGGER, AXIS_RIGHT_TRIGGER = range(6)
(BUTTON_SOUTH, BUTTON_EAST, BUTTON_WEST, BUTTON_NORTH, BUTTON_BACK, BUTTON_GUIDE, BUTTON_START,
 BUTTON_LEFT_STICK, BUTTON_RIGHT_STICK, BUTTON_LEFT_SHOULDER, BUTTON_RIGHT_SHOULDER,
 BUTTON_DPAD_UP, BUTTON_DPAD_DOWN, BUTTON_DPAD_LEFT, BUTTON_DPAD_RIGHT) = range(15)
AXIS_MAX = 32767

HINT_BACKGROUND_EVENTS = b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"

_PATTERNS = {"win32": ["SDL3.dll"], "darwin": ["libSDL3*.dylib"]}.get(sys.platform, ["libSDL3.so*"])


def find_library(blender_binary):
    """Finds the SDL3 that ships next to `blender_binary`, else the system's. Returns None when
    neither exists."""
    root = os.path.dirname(os.path.abspath(blender_binary))
    places = [root,
              os.path.join(root, "blender.shared"),               # Windows
              os.path.join(root, "lib"),                          # Linux
              os.path.join(root, "..", "Resources", "lib"),       # macOS app bundle
              os.path.join(root, "..", "Frameworks")]
    for place in places:
        for pattern in _PATTERNS:
            hits = sorted(glob.glob(os.path.join(place, pattern)))
            if hits:
                return os.path.normpath(hits[0])
    return ctypes.util.find_library("SDL3")


class SDL:
    """A loaded SDL3 with typed entry points. Raises OSError when SDL3 can't be found."""

    def __init__(self, blender_binary):
        path = find_library(blender_binary)
        if not path:
            raise OSError("No SDL3 found. This Blender doesn't ship one and none is installed on the system")
        lib = ctypes.CDLL(path)
        self.path = path

        def bind(name, restype, *argtypes):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = restype, list(argtypes)
            setattr(self, name, fn)

        p, u32, b = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool
        bind("SDL_GetVersion", ctypes.c_int)
        bind("SDL_GetError", ctypes.c_char_p)
        bind("SDL_SetHint", b, ctypes.c_char_p, ctypes.c_char_p)
        bind("SDL_Init", b, u32)
        bind("SDL_QuitSubSystem", None, u32)
        bind("SDL_free", None, p)
        bind("SDL_SetGamepadEventsEnabled", None, b)
        bind("SDL_SetJoystickEventsEnabled", None, b)
        bind("SDL_UpdateGamepads", None)
        bind("SDL_GetGamepads", ctypes.POINTER(u32), ctypes.POINTER(ctypes.c_int))
        bind("SDL_OpenGamepad", p, u32)
        bind("SDL_CloseGamepad", None, p)
        bind("SDL_GamepadConnected", b, p)
        bind("SDL_GetGamepadName", ctypes.c_char_p, p)
        bind("SDL_GetGamepadAxis", ctypes.c_int16, p, ctypes.c_int)
        bind("SDL_GetGamepadButton", b, p, ctypes.c_int)

    def version(self):
        v = self.SDL_GetVersion()
        return "%d.%d.%d" % (v // 1000000, v // 1000 % 1000, v % 1000)

    def error(self):
        e = self.SDL_GetError()
        return e.decode(errors="replace") if e else ""

    def gamepad_ids(self):
        count = ctypes.c_int(0)
        ids = self.SDL_GetGamepads(ctypes.byref(count))
        if not ids:
            return []
        try:
            return [ids[i] for i in range(count.value)]
        finally:
            self.SDL_free(ctypes.cast(ids, ctypes.c_void_p))
