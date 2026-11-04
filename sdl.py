# SPDX-License-Identifier: GPL-3.0-or-later
"""The few SDL3 gamepad functions this extension uses, bound with ctypes.

SDL3 itself is loaded from lib/ next to this file (the release zip bundles it), or from
the system when lib/ has no copy (Linux and macOS packages of SDL3).
"""

import ctypes
import ctypes.util
import os
import sys

INIT_GAMEPAD = 0x00002000

AXIS_LEFTX, AXIS_LEFTY, AXIS_RIGHTX, AXIS_RIGHTY, AXIS_LEFT_TRIGGER, AXIS_RIGHT_TRIGGER = range(6)
BUTTON_LEFT_SHOULDER, BUTTON_RIGHT_SHOULDER = 9, 10
AXIS_MAX = 32767

HINT_BACKGROUND_EVENTS = b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"

_NAMES = {"win32": "SDL3.dll", "darwin": "libSDL3.dylib"}


def _library_path():
    here = os.path.join(os.path.dirname(__file__), "lib", _NAMES.get(sys.platform, "libSDL3.so.0"))
    if os.path.exists(here):
        return here
    return ctypes.util.find_library("SDL3")


class SDL:
    """A loaded SDL3 with typed entry points. Raises OSError if SDL3 can't be found."""

    def __init__(self):
        path = _library_path()
        if not path:
            raise OSError("SDL3 not found: no lib/ copy and none installed on the system")
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
