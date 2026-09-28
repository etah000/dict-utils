[app]
title = "mdict-audio"
input = "src/mdict_audio_app/main.py"
project_dir = "."
exec_directory = "dist"

[python]
packages = mdict_audio_app

[qt]
plugins = Core,Gui,Widgets,Multimedia
exclude_plugins = Qml,Quick,WebEngine,Charts

[nuitka]
mode = standalone
# Build prerequisites: bin/ffmpeg.exe, bin/ffprobe.exe, and LICENSES/.
include_data_files = bin/ffmpeg.exe,bin/ffprobe.exe,LICENSES
