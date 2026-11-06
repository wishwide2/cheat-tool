# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
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
    name='AkasanWorkshop',
    console=False,
    upx=False,
    icon=str(root / 'ui_icon.ico'),
)
