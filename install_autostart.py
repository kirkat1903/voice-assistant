import sys
import winreg
from pathlib import Path

APP_NAME = "VoiceAssistant"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def pythonw_path():
    exe = Path(sys.executable)
    pythonw = exe.parent / "pythonw.exe"
    return str(pythonw) if pythonw.exists() else str(exe)


def install():
    script = Path(__file__).parent / "tray_app.py"
    command = f'"{pythonw_path()}" "{script}"'
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    winreg.CloseKey(key)
    print(f"Автозапуск добавлен:\n{command}")


def uninstall():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print("Автозапуск удалён")
    except FileNotFoundError:
        print("Автозапуск не был установлен")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "remove":
        uninstall()
    else:
        install()
