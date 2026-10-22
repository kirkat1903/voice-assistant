"""Saves an API key for Aiko's conversation mode to secrets.json (git-ignored).

Run it yourself in a terminal:   venv\\Scripts\\python.exe set_api_key.py
The key is typed hidden and is only ever sent to the service you pick.

Free options:
  Groq    https://console.groq.com/keys          fast Llama models, free tier
  Gemini  https://aistudio.google.com/apikey     free tier (Google may use free-tier data to improve its products)
No key at all: install Ollama (https://ollama.com), run `ollama pull qwen2.5:3b` - fully local.
Paid: Anthropic https://console.anthropic.com
"""
import getpass
import json
from pathlib import Path

SECRETS = Path(__file__).parent / "secrets.json"
SERVICES = {
    "1": ("groq_api_key", "Groq (бесплатно, быстро)", "gsk_"),
    "2": ("gemini_api_key", "Google Gemini (бесплатный тариф)", "AIza"),
    "3": ("openrouter_api_key", "OpenRouter (есть бесплатные модели)", "sk-or-"),
    "4": ("anthropic_api_key", "Anthropic Claude (платно)", "sk-ant-"),
}


def main():
    print("Для какого сервиса ключ?")
    for number, (_, title, _) in SERVICES.items():
        print(f"  {number} — {title}")
    choice = input("Номер: ").strip()
    if choice not in SERVICES:
        print("Не понял выбор, выхожу.")
        return
    field, title, prefix = SERVICES[choice]
    print("Ключ вводится скрытно (символы на экране не появляются).")
    key = getpass.getpass(f"Вставь ключ для «{title}»: ").strip()
    if not key:
        print("Ничего не введено, выхожу.")
        return
    if not key.startswith(prefix) and input(f"Обычно ключ начинается с {prefix}… Сохранить всё равно? [y/N] ").strip().lower() != "y":
        return
    try:
        data = json.loads(SECRETS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[field] = key
    SECRETS.write_text(json.dumps(data), encoding="utf-8")
    print(f"Сохранено в {SECRETS.name}. Теперь перезапусти Айко: значок в трее → «Перезапустить».")


if __name__ == "__main__":
    main()
