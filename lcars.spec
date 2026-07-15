# PyInstaller spec for a portable, no-Python-required build of LCARS Terminal
# Interface. Build with:
#
#   .\.venv\Scripts\pyinstaller.exe lcars.spec --noconfirm
#
# Output goes to dist\lcars\ (onedir build -- see build.ps1 for rationale).
# Copy that whole folder anywhere on a Windows machine and run lcars.exe;
# no Python installation is required on the target machine.

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Non-.py assets that app.py loads at runtime via Path(__file__).parent --
# PyInstaller only auto-collects .py modules, so these must be listed
# explicitly or the frozen build will fail to find the CSS / prompt script.
datas = [
    ("lcars_tui/lcars.tcss", "lcars_tui"),
    ("lcars_tui/assets/lcars_prompt.ps1", "lcars_tui/assets"),
]

a = Analysis(
    ["lcars_tui/__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lcars",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # this is a terminal UI -- must keep a console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="lcars",
)
