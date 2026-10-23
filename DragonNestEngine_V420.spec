# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

datas = [
    ('dragon_nest_builder\\data\\modular', 'dragon_nest_builder\\data\\modular'),
    ('dragon_nest_builder\\data\\live_dps_profiles', 'dragon_nest_builder\\data\\live_dps_profiles'),
    ('tools\\capture_live_dps_v576.ps1', 'tools'),
]

excluded_qt_modules = ['PySide6', 'shiboken6', 'qfluentwidgets']
addon = Path('third_party/skill_hit_tool/app')
for package in ('skill_hit_tool', 'act_hit_timeline_comparator', 'skill_timeline_viewer'):
    for path in (addon / package).rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.json'}:
            datas.append((str(path), str(Path('third_party/skill_hit_tool/app') / path.parent.relative_to(addon))))

a = Analysis(
    ['v420_runtime.py'],
    pathex=[str(addon.resolve())],
    binaries=[],
    datas=datas,
    # The V4.43 theory planner intentionally loads the mature V4.01 scoring
    # methods at runtime behind Qt placeholders.  PyInstaller cannot discover
    # that importlib-based dependency, so the frozen engine must include it
    # explicitly even though the actual Qt runtime remains excluded.
    # The countdown build has no Qt UI and no scoring pipeline, so neither the
    # desktop page nor the team-score dialog needs to be frozen in.
    hiddenimports=[
        'win32timezone',
        'html',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_qt_modules,
    # Keep Python bytecode as individual files in the one-dir runtime instead
    # of appending a large compressed PYZ archive to the launcher executable.
    # Besides making hidden-import failures easier to diagnose, this produces
    # a much smaller, less opaque launcher for antivirus scanners.
    noarchive=True,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DragonNestEngine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='dragon_nest_builder\\assets\\app_icon_build.ico',
    version='dragon_nest_builder\\build_support\\windows_version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DragonNestEngine',
)
