# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['version2.0.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/app.ico', 'assets'),      # 窗口/任务栏图标
           ('assets/logo.png', 'assets')],      # ico 读不到时的 PNG 兜底
    hiddenimports=['entity', 'mapper', 'mapper.t_points_mapper',
                   'mapper.t_shop_mapper',
                   'service', 'service.dp', 'service.migrate',
                   'service.backup', 'excel_io', 'excel_import',
                   'points_shortcuts', 'ui_points_shortcuts',
                   'ui_admin', 'ui_admin_import', 'ui_admin_stu', 'ui_background',
                   'ui_backup', 'ui_history', 'ui_history_picker',
                   'ui_points', 'ui_season', 'ui_season_admin',
                   'ui_settings', 'ui_shop', 'ui_theme', 'ui_widgets'],
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
    name='dmxt',
    icon='assets/app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
