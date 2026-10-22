"""Creates the «Айко» shortcut on the Desktop.

Double-click it to start Aiko; if she is already running it brings the widget back instead.
Run again any time to re-create the shortcut:  venv\\Scripts\\python.exe make_shortcut.py
"""
from pathlib import Path

import win32com.client

BASE = Path(__file__).parent


def main():
    shell = win32com.client.Dispatch("WScript.Shell")
    desktop = Path(shell.SpecialFolders("Desktop"))
    link = shell.CreateShortcut(str(desktop / "Айко.lnk"))
    link.TargetPath = str(BASE / "venv" / "Scripts" / "pythonw.exe")
    link.Arguments = f'"{BASE / "tray_app.py"}"'
    link.WorkingDirectory = str(BASE)
    link.IconLocation = f"{BASE / 'icon.ico'},0"
    link.Description = "Голосовой помощник Айко: запустить или вернуть виджет"
    link.WindowStyle = 7                     # minimized: no flash of a window
    link.Save()
    print(f"Ярлык создан: {desktop / 'Айко.lnk'}")


if __name__ == "__main__":
    main()
