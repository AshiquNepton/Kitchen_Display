# PyInstaller spec: build with  pyinstaller kds.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

hidden = (collect_submodules("kds") + collect_submodules("kds_project") +
          ["django.contrib.staticfiles", "django.templatetags.static", "waitress", "pyodbc",
           # win32print / pywin32 — needed for raw ESC/POS printing to USB & Windows printers
           "win32print", "win32api", "win32con", "pywintypes"])

a = Analysis(
    ["run_kds.py"], pathex=["."],
    datas=[("kds/templates", "kds/templates"), ("kds/static", "kds/static")],
    hiddenimports=hidden, excludes=["tkinter", "unittest", "test"],
)
pyz = PYZ(a.pure)
# One single KDS.exe (everything is packed inside it), so the install folder shows no project files.
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="KDS", console=False, upx=False, icon="kds/static/kds/icon.ico")
