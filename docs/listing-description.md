Fly around Blender's 3D viewport with a gamepad. PlayStation, Xbox, and Switch controllers work with no extra drivers or software. 

### Controls

| Input | Does |
| --- | --- |
| Left stick | Moves forward and back and strafes left and right |
| Right stick | Looks around |
| Bumpers | Move down and up |
| Right trigger | Speeds up the more you press it |
| Left trigger | Slows down the more you press it |
| Triangle / Y | Toggles perspective and orthographic |

In an orthographic view the left stick zooms instead of moving. When you look through the scene camera (numpad 0), the gamepad moves the camera itself.

### Settings

Open **Edit > Preferences > Add-ons > Gamepad Navigation** to change move and look speed, sprint and crawl strength, the stick dead zone, and inverted look. Each button can run a ready-made action or any Blender operator you type, such as `view3d.view_axis(type='TOP')`.

### How it works

The add-on reads the controller through the SDL3 library that ships with Blender, using Python's `ctypes`. It bundles no binaries, downloads nothing, and never connects to the internet. On Windows it checks which app is in front so it can ignore the controller while you use another app. You can turn this off in the settings. 

Gamepad events are polled every second until a gamepad is detected. Then it is polled 60Hz after connected. These poll rates and various other internal settings and defaults can be tweaked in `settings.ini`.
