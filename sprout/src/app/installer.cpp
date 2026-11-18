// Установка хуков Claude Code в ~/.claude/settings.json.
// Перед записью: резервная копия + подтверждение. Чужие хуки и настройки не трогаем.
#include "app.h"
#include <shlobj.h>

namespace {

// Только устоявшиеся события: неизвестный ключ может сломать валидацию settings.json.
struct HookSpec { const char* event; bool async; int timeout; };
const HookSpec kHooks[] = {
    {"SessionStart",      true,  10},
    {"UserPromptSubmit",  true,  10},
    {"PreToolUse",        true,  10},
    {"PostToolUse",       true,  10},
    {"PermissionRequest", false, 600},   // ждём решения в островке
    {"Notification",      true,  10},
    {"Stop",              true,  10},
    {"SubagentStop",      true,  10},
    {"SessionEnd",        true,  10},
};

const char* kMarker = "sprout-hook.exe";

std::wstring HomeDir() {
    PWSTR p = nullptr;
    std::wstring out;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Profile, 0, nullptr, &p))) out = p;
    if (p) CoTaskMemFree(p);
    return out;
}

std::wstring InstalledHookPath() { return LocalAppDataDir() + L"\\bin\\sprout-hook.exe"; }

bool ReadFileUtf8(const std::wstring& path, std::string& out) {
    if (!sprout::ReadWholeFile(path, out)) return false;
    if (out.size() >= 3 && (uint8_t)out[0] == 0xEF && (uint8_t)out[1] == 0xBB && (uint8_t)out[2] == 0xBF)
        out.erase(0, 3);
    return true;
}

bool WriteFileUtf8(const std::wstring& path, const std::string& data) {
    std::wstring tmp = path + L".sprout-tmp";
    if (!sprout::WriteWholeFile(tmp, data)) return false;
    return MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE;
}

std::wstring Timestamp() {
    SYSTEMTIME t;
    GetLocalTime(&t);
    wchar_t buf[64];
    wsprintfW(buf, L"%04d%02d%02d-%02d%02d%02d", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    return buf;
}

bool IsSproutEntry(const ojson& group) {
    if (!group.is_object() || !group.contains("hooks") || !group["hooks"].is_array()) return false;
    for (const auto& h : group["hooks"]) {
        if (h.is_object() && h.contains("command") && h["command"].is_string() &&
            h["command"].get<std::string>().find(kMarker) != std::string::npos)
            return true;
    }
    return false;
}

// Удаляет наши записи. Возвращает число удалённых групп.
int StripSprout(ojson& root) {
    int removed = 0;
    if (!root.contains("hooks") || !root["hooks"].is_object()) return 0;
    ojson& hooks = root["hooks"];
    std::vector<std::string> emptyEvents;
    for (auto it = hooks.begin(); it != hooks.end(); ++it) {
        if (!it.value().is_array()) continue;
        ojson kept = ojson::array();
        for (auto& g : it.value()) {
            if (IsSproutEntry(g)) ++removed;
            else kept.push_back(g);
        }
        it.value() = kept;
        if (kept.empty()) emptyEvents.push_back(it.key());
    }
    for (const auto& e : emptyEvents) hooks.erase(e);
    if (hooks.empty()) root.erase("hooks");
    return removed;
}

bool LoadSettings(HWND owner, const std::wstring& path, ojson& root, bool& existed) {
    std::string text;
    existed = ReadFileUtf8(path, text);
    if (!existed || text.find_first_not_of(" \t\r\n") == std::string::npos) {
        root = ojson::object();
        return true;
    }
    root = ojson::parse(text, nullptr, false);
    if (root.is_discarded() || !root.is_object()) {
        std::wstring msg = L"Не получилось прочитать\n" + path +
                           L"\n\nФайл содержит ошибку JSON. Sprout ничего не менял.";
        MessageBoxW(owner, msg.c_str(), L"Sprout", MB_ICONWARNING);
        return false;
    }
    return true;
}

bool Backup(HWND owner, const std::wstring& path, std::wstring& backupPath) {
    backupPath = path + L".sprout-backup-" + Timestamp();
    if (!CopyFileW(path.c_str(), backupPath.c_str(), FALSE)) {
        MessageBoxW(owner, L"Не удалось сделать резервную копию settings.json. Ничего не изменено.",
                    L"Sprout", MB_ICONERROR);
        return false;
    }
    return true;
}

bool CopyHookExe(HWND owner) {
    std::wstring src = ExeDir() + L"\\sprout-hook.exe";
    if (GetFileAttributesW(src.c_str()) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(owner, L"Рядом с Sprout.exe нет sprout-hook.exe. Положите оба файла в одну папку.",
                    L"Sprout", MB_ICONERROR);
        return false;
    }
    std::wstring dir = LocalAppDataDir() + L"\\bin";
    CreateDirectoryW(dir.c_str(), nullptr);
    std::wstring dst = InstalledHookPath();
    if (lstrcmpiW(src.c_str(), dst.c_str()) == 0) return true;
    if (CopyFileW(src.c_str(), dst.c_str(), FALSE)) return true;
    // Старый хук прямо сейчас запущен Claude Code -> отодвигаем его в сторону.
    std::wstring old = dst + L".old";
    DeleteFileW(old.c_str());
    if (MoveFileExW(dst.c_str(), old.c_str(), MOVEFILE_REPLACE_EXISTING) && CopyFileW(src.c_str(), dst.c_str(), FALSE))
        return true;
    MessageBoxW(owner, L"Не удалось скопировать sprout-hook.exe в папку LocalAppData.", L"Sprout", MB_ICONERROR);
    return false;
}

} // namespace

std::wstring ClaudeSettingsPath() {
    wchar_t env[MAX_PATH * 2] = {};
    if (GetEnvironmentVariableW(L"CLAUDE_CONFIG_DIR", env, MAX_PATH * 2) > 0)
        return std::wstring(env) + L"\\settings.json";
    return HomeDir() + L"\\.claude\\settings.json";
}

bool HooksInstalled() {
    std::string text;
    if (!ReadFileUtf8(ClaudeSettingsPath(), text)) return false;
    return text.find(kMarker) != std::string::npos;
}

void InstallHooks(HWND owner) {
    const std::wstring path = ClaudeSettingsPath();
    ojson root;
    bool existed = false;
    if (!LoadSettings(owner, path, root, existed)) return;

    std::wstring events;
    for (const auto& h : kHooks) events += L"  • " + sprout::Widen(h.event) + (h.async ? L"" : L"  (ждёт твоего решения)") + L"\n";
    std::wstring prompt =
        L"Sprout добавит хуки в\n" + path + L"\n\nСобытия:\n" + events +
        L"\nХук: " + InstalledHookPath() +
        L"\n\nОстальные настройки и чужие хуки не меняются." +
        (existed ? L" Перед записью будет сделана резервная копия." : L"") +
        L"\nЕсли Sprout не запущен, хук сразу завершается и Claude Code работает как обычно.\n\nПродолжить?";
    if (MessageBoxW(owner, prompt.c_str(), L"Установка хуков Claude Code", MB_YESNO | MB_ICONQUESTION) != IDYES)
        return;

    if (!CopyHookExe(owner)) return;

    std::wstring backupPath;
    if (existed && !Backup(owner, path, backupPath)) return;

    StripSprout(root);   // переустановка — без дублей
    if (!root.contains("hooks") || !root["hooks"].is_object()) root["hooks"] = ojson::object();
    const std::string exe = sprout::Narrow(InstalledHookPath());
    for (const auto& h : kHooks) {
        ojson cmd;
        cmd["type"] = "command";
        cmd["command"] = exe;
        cmd["args"] = ojson::array({"--sprout"});   // exec-форма: без оболочки и проблем с пробелами в пути
        cmd["timeout"] = h.timeout;
        if (h.async) cmd["async"] = true;
        ojson group;
        group["hooks"] = ojson::array({cmd});
        ojson& arr = root["hooks"][h.event];
        if (!arr.is_array()) arr = ojson::array();
        arr.push_back(group);
    }

    std::wstring dir = path.substr(0, path.find_last_of(L"\\/"));
    SHCreateDirectoryExW(nullptr, dir.c_str(), nullptr);
    if (!WriteFileUtf8(path, root.dump(2, ' ', false, ojson::error_handler_t::replace) + "\n")) {
        MessageBoxW(owner, L"Не удалось записать settings.json.", L"Sprout", MB_ICONERROR);
        return;
    }
    std::wstring done = L"Готово! Хуки установлены.\n\nПерезапусти сессии Claude Code, чтобы они подхватили хуки.";
    if (!backupPath.empty()) done += L"\n\nРезервная копия:\n" + backupPath;
    MessageBoxW(owner, done.c_str(), L"Sprout", MB_ICONINFORMATION);
}

void UninstallHooks(HWND owner) {
    const std::wstring path = ClaudeSettingsPath();
    ojson root;
    bool existed = false;
    if (!LoadSettings(owner, path, root, existed)) return;
    if (!existed) { MessageBoxW(owner, L"settings.json не найден — удалять нечего.", L"Sprout", MB_ICONINFORMATION); return; }

    ojson copy = root;
    int n = StripSprout(copy);
    if (n == 0) { MessageBoxW(owner, L"Хуков Sprout в settings.json нет.", L"Sprout", MB_ICONINFORMATION); return; }
    if (MessageBoxW(owner, L"Удалить хуки Sprout из settings.json? Остальное останется как есть.",
                    L"Sprout", MB_YESNO | MB_ICONQUESTION) != IDYES)
        return;
    std::wstring backupPath;
    if (!Backup(owner, path, backupPath)) return;
    if (!WriteFileUtf8(path, copy.dump(2, ' ', false, ojson::error_handler_t::replace) + "\n")) {
        MessageBoxW(owner, L"Не удалось записать settings.json.", L"Sprout", MB_ICONERROR);
        return;
    }
    std::wstring done = L"Хуки удалены.\n\nРезервная копия:\n" + backupPath;
    MessageBoxW(owner, done.c_str(), L"Sprout", MB_ICONINFORMATION);
}
