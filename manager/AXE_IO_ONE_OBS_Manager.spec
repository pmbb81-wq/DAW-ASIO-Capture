# -*- mode: python ; coding: utf-8 -*-
# Nagrywarka (DAW) - tylko odczyt wspolnej pamieci proxy. Bez sounddevice.
# ffmpeg.exe jest dolaczany, zeby MP3/FLAC/OGG/M4A dzialaly bez instalacji.
datas = [('ffmpeg', 'ffmpeg')]
binaries = []
hiddenimports = []


a = Analysis(
    ['daw_recorder.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AXE_IO_ONE_OBS_Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['ffmpeg.exe'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)