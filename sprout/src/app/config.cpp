// Настройки, ключ API (Диспетчер учётных данных Windows), автозапуск, диалог ввода.
#include "app.h"
#include <wincred.h>
#include <shlobj.h>

namespace {
Config g_cfg;
const wchar_t* kCredTarget = L"Sprout/AnthropicApiKey";
const wchar_t* kRunKey = L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const wchar_t* kRunValue = L"Sprout";

std::wstring KnownFolder(REFKNOWNFOLDERID id) {
    PWSTR p = nullptr;
    std::wstring out;
    if (SUCCEEDED(SHGetKnownFolderPath(id, 0, nullptr, &p))) out = p;
    if (p) CoTaskMemFree(p);
    return out;
}

std::wstring ConfigPath() { return AppDataDir() + L"\\config.json"; }
} // namespace

Config& Cfg() { return g_cfg; }

std::wstring AppDataDir() {
    std::wstring d = KnownFolder(FOLDERID_RoamingAppData) + L"\\Sprout";
    CreateDirectoryW(d.c_str(), nullptr);
    return d;
}

std::wstring LocalAppDataDir() {
    std::wstring d = KnownFolder(FOLDERID_LocalAppData) + L"\\Sprout";
    CreateDirectoryW(d.c_str(), nullptr);
    return d;
}

std::wstring ExeDir() {
    wchar_t buf[MAX_PATH * 2] = {};
    GetModuleFileNameW(nullptr, buf, MAX_PATH * 2);
    std::wstring s = buf;
    size_t pos = s.find_last_of(L"\\/");
    return pos == std::wstring::npos ? s : s.substr(0, pos);
}

void LoadConfig() {
    std::string text;
    if (!sprout::ReadWholeFile(ConfigPath(), text)) return;
    json j = json::parse(text, nullptr, false);
    if (j.is_discarded() || !j.is_object()) return;
    g_cfg.sounds = j.value("sounds", g_cfg.sounds);
    g_cfg.autoExpand = j.value("autoExpand", g_cfg.autoExpand);
    g_cfg.permissionTimeoutSec = j.value("permissionTimeoutSec", g_cfg.permissionTimeoutSec);
    g_cfg.model = j.value("model", g_cfg.model);
    g_cfg.maxTokens = j.value("maxTokens", g_cfg.maxTokens);
    g_cfg.firstRunDone = j.value("firstRunDone", g_cfg.firstRunDone);
    g_cfg.petMode = j.value("petMode", g_cfg.petMode);
    g_cfg.petX = j.value("petX", g_cfg.petX);
    g_cfg.petY = j.value("petY", g_cfg.petY);
}

void SaveConfig() {
    ojson j;
    j["sounds"] = g_cfg.sounds;
    j["autoExpand"] = g_cfg.autoExpand;
    j["permissionTimeoutSec"] = g_cfg.permissionTimeoutSec;
    j["model"] = g_cfg.model;
    j["maxTokens"] = g_cfg.maxTokens;
    j["firstRunDone"] = g_cfg.firstRunDone;
    j["petMode"] = g_cfg.petMode;
    j["petX"] = g_cfg.petX;
    j["petY"] = g_cfg.petY;
    sprout::WriteWholeFile(ConfigPath(), j.dump(2));
}

bool SaveApiKey(const std::wstring& key) {
    if (key.empty()) { CredDeleteW(kCredTarget, CRED_TYPE_GENERIC, 0); return true; }
    std::string blob = sprout::Narrow(key);
    CREDENTIALW c{};
    c.Type = CRED_TYPE_GENERIC;
    c.TargetName = const_cast<LPWSTR>(kCredTarget);
    c.CredentialBlobSize = (DWORD)blob.size();
    c.CredentialBlob = (LPBYTE)blob.data();
    c.Persist = CRED_PERSIST_LOCAL_MACHINE;
    c.UserName = const_cast<LPWSTR>(L"Sprout");
    return CredWriteW(&c, 0) != FALSE;
}

std::string LoadApiKey() {
    PCREDENTIALW c = nullptr;
    if (CredReadW(kCredTarget, CRED_TYPE_GENERIC, 0, &c)) {
        std::string key((const char*)c->CredentialBlob, c->CredentialBlobSize);
        CredFree(c);
        if (!key.empty()) return key;
    }
    wchar_t env[512] = {};
    if (GetEnvironmentVariableW(L"ANTHROPIC_API_KEY", env, 512) > 0) return sprout::Narrow(env);
    return {};
}

bool IsAutostartEnabled() {
    HKEY k;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, kRunKey, 0, KEY_READ, &k) != ERROR_SUCCESS) return false;
    LONG r = RegQueryValueExW(k, kRunValue, nullptr, nullptr, nullptr, nullptr);
    RegCloseKey(k);
    return r == ERROR_SUCCESS;
}

void SetAutostart(bool on) {
    HKEY k;
    if (RegCreateKeyExW(HKEY_CURRENT_USER, kRunKey, 0, nullptr, 0, KEY_WRITE, nullptr, &k, nullptr) != ERROR_SUCCESS)
        return;
    if (on) {
        wchar_t exe[MAX_PATH * 2] = {};
        GetModuleFileNameW(nullptr, exe, MAX_PATH * 2);
        std::wstring cmd = L"\"" + std::wstring(exe) + L"\"";
        RegSetValueExW(k, kRunValue, 0, REG_SZ, (const BYTE*)cmd.c_str(), (DWORD)((cmd.size() + 1) * sizeof(wchar_t)));
    } else {
        RegDeleteValueW(k, kRunValue);
    }
    RegCloseKey(k);
}

// ---------- Простой модальный диалог ввода строки (без .rc-ресурсов) ----------
namespace {
struct PromptState {
    HWND edit = nullptr;
    std::wstring* value = nullptr;
    bool ok = false;
    bool done = false;
};

LRESULT CALLBACK PromptProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    auto* st = (PromptState*)GetWindowLongPtrW(h, GWLP_USERDATA);
    switch (m) {
    case WM_COMMAND:
        if (LOWORD(w) == IDOK && st) {
            int len = GetWindowTextLengthW(st->edit);
            std::wstring v(len, L'\0');
            GetWindowTextW(st->edit, &v[0], len + 1);
            *st->value = v;
            st->ok = true;
            st->done = true;
            DestroyWindow(h);
        } else if (LOWORD(w) == IDCANCEL && st) {
            st->done = true;
            DestroyWindow(h);
        }
        return 0;
    case WM_CLOSE:
        if (st) st->done = true;
        DestroyWindow(h);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
} // namespace

bool PromptText(HWND owner, const wchar_t* title, const wchar_t* label, std::wstring& value, bool password) {
    HINSTANCE inst = GetModuleHandleW(nullptr);
    static bool registered = false;
    if (!registered) {
        WNDCLASSW wc{};
        wc.lpfnWndProc = PromptProc;
        wc.hInstance = inst;
        wc.lpszClassName = L"SproutPrompt";
        wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
        RegisterClassW(&wc);
        registered = true;
    }
    const int W = 440, H = 170;
    int sx = (GetSystemMetrics(SM_CXSCREEN) - W) / 2, sy = (GetSystemMetrics(SM_CYSCREEN) - H) / 3;
    HWND dlg = CreateWindowExW(WS_EX_DLGMODALFRAME | WS_EX_TOPMOST, L"SproutPrompt", title,
                               WS_POPUP | WS_CAPTION | WS_SYSMENU, sx, sy, W, H, owner, nullptr, inst, nullptr);
    PromptState st;
    st.value = &value;
    SetWindowLongPtrW(dlg, GWLP_USERDATA, (LONG_PTR)&st);

    HFONT font = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    HWND lbl = CreateWindowW(L"STATIC", label, WS_CHILD | WS_VISIBLE, 14, 12, W - 40, 40, dlg, nullptr, inst, nullptr);
    st.edit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", value.c_str(),
                              WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | (password ? ES_PASSWORD : 0),
                              14, 56, W - 44, 26, dlg, nullptr, inst, nullptr);
    HWND ok = CreateWindowW(L"BUTTON", L"OK", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
                            W - 220, 94, 90, 28, dlg, (HMENU)IDOK, inst, nullptr);
    HWND cancel = CreateWindowW(L"BUTTON", L"Отмена", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                                W - 122, 94, 90, 28, dlg, (HMENU)IDCANCEL, inst, nullptr);
    for (HWND c : {lbl, st.edit, ok, cancel}) SendMessageW(c, WM_SETFONT, (WPARAM)font, TRUE);

    if (owner) EnableWindow(owner, FALSE);
    ShowWindow(dlg, SW_SHOW);
    SetForegroundWindow(dlg);
    SetFocus(st.edit);

    MSG msg;
    while (!st.done && GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_RETURN && IsChild(dlg, msg.hwnd)) {
            SendMessageW(dlg, WM_COMMAND, IDOK, 0);
            continue;
        }
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_ESCAPE && IsChild(dlg, msg.hwnd)) {
            SendMessageW(dlg, WM_COMMAND, IDCANCEL, 0);
            continue;
        }
        if (!IsDialogMessageW(dlg, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    if (owner) { EnableWindow(owner, TRUE); SetForegroundWindow(owner); }
    DeleteObject(font);
    return st.ok;
}
