# SPDX-License-Identifier: GPL-3.0-or-later
"""Download the official SDL3 release for Windows x64 into lib/ (SDL3.dll + its licence).

    python tools/fetch_sdl.py

Pinned to one release and checked against its SHA-256, so a build always bundles the
same SDL. To move to a newer SDL, change VERSION and SHA256 (from the release's zip).
"""

import hashlib
import io
import os
import sys
import urllib.request
import zipfile

VERSION = "3.4.20"
SHA256 = "368bed8b24e44ad9ea372990814f2343fcfb9e468abb7f8f4a3bc146c7304752"
URL = "https://github.com/libsdl-org/SDL/releases/download/release-%s/SDL3-%s-win32-x64.zip" % (VERSION, VERSION)
LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")


def main():
    print("Downloading", URL)
    data = urllib.request.urlopen(URL).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        sys.exit("SHA-256 mismatch: got %s, expected %s" % (digest, SHA256))
    os.makedirs(LIB, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for src, dst in (("SDL3.dll", "SDL3.dll"), ("LICENSE.txt", "LICENSE-SDL3.txt")):
            with open(os.path.join(LIB, dst), "wb") as f:
                f.write(z.read(src))
            print("wrote lib/" + dst)


if __name__ == "__main__":
    main()
