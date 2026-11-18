// Общие утилиты для Sprout.exe и sprout-hook.exe
#pragma once
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <string>

namespace sprout {

inline std::wstring Widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

inline std::string Narrow(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

// Имя канала уникально для пользователя Windows: хуки одного пользователя
// не попадут в Sprout другого пользователя на той же машине.
inline std::wstring PipeName() {
    wchar_t user[257] = {};
    DWORD n = 257;
    if (!GetUserNameW(user, &n)) lstrcpyW(user, L"user");
    std::wstring name = L"\\\\.\\pipe\\sprout-claude-";
    for (wchar_t* p = user; *p; ++p) name += (*p == L'\\' || *p == L'/') ? L'_' : *p;
    return name;
}

constexpr DWORD kPipeBufferSize = 64 * 1024;

// Читает одно сообщение из канала в режиме сообщений (любого размера).
inline bool ReadPipeMessage(HANDLE h, std::string& out) {
    char buf[8192];
    for (;;) {
        DWORD n = 0;
        BOOL ok = ReadFile(h, buf, sizeof(buf), &n, nullptr);
        if (ok) { out.append(buf, n); return true; }
        if (GetLastError() == ERROR_MORE_DATA) { out.append(buf, n); continue; }
        return false;
    }
}

inline bool WriteAll(HANDLE h, const std::string& s) {
    size_t off = 0;
    while (off < s.size()) {
        DWORD w = 0;
        if (!WriteFile(h, s.data() + off, (DWORD)(s.size() - off), &w, nullptr)) return false;
        off += w;
    }
    return true;
}

// Чтение/запись файла целиком через Win32 (пути с кириллицей работают в MSVC и MinGW).
inline bool ReadWholeFile(const std::wstring& path, std::string& out) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    out.clear();
    char buf[16384];
    DWORD n = 0;
    while (ReadFile(h, buf, sizeof(buf), &n, nullptr) && n > 0) out.append(buf, n);
    CloseHandle(h);
    return true;
}

inline bool WriteWholeFile(const std::wstring& path, const std::string& data) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    bool ok = WriteAll(h, data) && FlushFileBuffers(h);
    CloseHandle(h);
    return ok;
}

} // namespace sprout
