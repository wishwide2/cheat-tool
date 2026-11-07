# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from runpy import run_path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
app = run_path(str(root / 'workshop_paths.py'))
version = (*map(int, app['APP_VERSION'].split('.')), 0)
resources = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version, prodvers=version),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', app['APP_NAME']),
            StringStruct('ProductName', app['APP_NAME']),
            StringStruct('FileVersion', app['APP_VERSION']),
            StringStruct('ProductVersion', app['APP_VERSION']),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)
analysis = Analysis(
    [str(root / 'akasan_editor.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'ui_icon.png'), '.'), (str(root / 'ui_assets'), 'ui_assets')],
    hiddenimports=['lz4.block'],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name='AkasanTool',
    console=False,
    upx=False,
    icon=str(root / 'ui_icon.ico'),
    version=resources,
)
