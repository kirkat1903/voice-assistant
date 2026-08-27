import threading

import pystray
from PIL import Image, ImageDraw

from voice_assistant import VoiceAssistant, load_config


def make_icon_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=color)
    return img


ICON_ACTIVE = make_icon_image("#4CAF50")
ICON_PAUSED = make_icon_image("#FFC107")


class TrayApp:
    def __init__(self):
        self.config = load_config()
        self.assistant = VoiceAssistant(self.config)
        self.thread = None
        self.icon = pystray.Icon(
            "voice_assistant",
            ICON_ACTIVE,
            "Голосовой помощник",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "На паузе", self.toggle_pause, checked=lambda item: self.assistant.paused
                ),
                pystray.MenuItem("Перезапустить", self.restart),
                pystray.MenuItem("Выход", self.quit_app),
            ),
        )

    def start_thread(self):
        self.thread = threading.Thread(target=self.assistant.loop, daemon=True)
        self.thread.start()

    def toggle_pause(self, icon, item):
        if self.assistant.paused:
            self.assistant.resume()
            icon.icon = ICON_ACTIVE
        else:
            self.assistant.pause()
            icon.icon = ICON_PAUSED

    def restart(self, icon, item):
        self.assistant.stop()
        if self.thread:
            self.thread.join(timeout=3)
        self.start_thread()
        icon.icon = ICON_ACTIVE

    def quit_app(self, icon, item):
        self.assistant.stop()
        icon.stop()

    def run(self):
        self.start_thread()
        self.icon.run()


if __name__ == "__main__":
    TrayApp().run()
