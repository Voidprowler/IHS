# -*- mode: python ; coding: utf-8 -*-
# Build from Codes/nvml-build (the folder that holds main.py):
#     pyinstaller Intelligent_Hardware_Sentinel.spec
#
# Changes from the spec used for the earlier build (not yet tested on the final commit):
#   - pathex is "." instead of a path on the author's machine
#   - gpu_nvml.py and telemetry_log.py are added (main.py and sensors.py import them)
#   - upx is False, because UPX-packed one-file builds are often flagged by antivirus tools

datas = [
    ('LibreHardwareMonitorLib.dll', '.'),
    ('HidSharp.dll', '.'),
    ('System.Memory.dll', '.'),
    ('RAMSPDToolkit-NDD.dll', '.'),
    ('System.Runtime.CompilerServices.Unsafe.dll', '.'),
    ('System.Numerics.Vectors.dll', '.'),
    ('DiskInfoToolkit.dll', '.'),
    ('BlackSharp.Core.dll', '.'),
    ('Decepticons.ico', '.'),
    ('alerts.py', '.'),
    ('sensors.py', '.'),
    ('gpu_nvml.py', '.'),
    ('telemetry_log.py', '.'),
    ('config.py', '.'),
    ('dashboard.py', '.'),
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=['alerts', 'sensors', 'gpu_nvml', 'telemetry_log', 'config', 'dashboard',
                   'psutil', 'clr', 'pystray', 'PIL', 'PIL.Image',
                   'PIL.ImageDraw', 'winotify', 'wmi'],
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
    name='Intelligent_Hardware_Sentinel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon='Decepticons.ico',
)
