// sprout-hook.exe — мост между хуками Claude Code и Sprout.exe.
//
// Claude Code запускает этот exe на каждое событие и передаёт JSON в stdin.
// Хук пересылает событие в Sprout.exe по именованному каналу.
//
// Главное правило: хук никогда не тормозит Claude Code.
//  * Sprout не запущен  -> выходим сразу, без вывода (Claude Code работает как обычно).
//  * Обычное событие    -> отправили и вышли.
//  * PermissionRequest  -> ждём решения пользователя в островке.
//                          "pass" или обрыв связи -> без вывода, Claude Code покажет
//                          обычный диалог в терминале.

#include "../common/shared.h"
#include <tlhelp32.h>
#include <unordered_map>
#include <vector>
#include "json.hpp"

using json = nlohmann::json;

static std::string ReadAllStdin() {
    std::string out;
    HANDLE h = GetStdHandle(STD_INPUT_HANDLE);
    if (h == nullptr || h == INVALID_HANDLE_VALUE) return out;
    char buf[16384];
    DWORD n = 0;
    while (ReadFile(h, buf, sizeof(buf), &n, nullptr) && n > 0) out.append(buf, n);
    return out;
}

// Цепочка родительских процессов: по ней Sprout находит окно терминала
// (Windows Terminal, VS Code, conhost), чтобы «прыгнуть» в него по клику.
static std::vector<DWORD> AncestorPids() {
    std::vector<DWORD> chain;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return chain;
    std::unordered_map<DWORD, DWORD> parent;
    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snap, &pe)) {
        do { parent[pe.th32ProcessID] = pe.th32ParentProcessID; } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    DWORD cur = GetCurrentProcessId();
    for (int i = 0; i < 16; ++i) {
        auto it = parent.find(cur);
        if (it == parent.end() || it->second == 0 || it->second == cur) break;
        cur = it->second;
        bool seen = false;
        for (DWORD p : chain) if (p == cur) seen = true;
        if (seen) break;
        chain.push_back(cur);
    }
    return chain;
}

static HANDLE ConnectToSprout() {
    const std::wstring name = sprout::PipeName();
    for (int attempt = 0; attempt < 3; ++attempt) {
        HANDLE h = CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                               OPEN_EXISTING, 0, nullptr);
        if (h != INVALID_HANDLE_VALUE) {
            DWORD mode = PIPE_READMODE_MESSAGE;
            SetNamedPipeHandleState(h, &mode, nullptr, nullptr);
            return h;
        }
        // Канала нет -> Sprout не запущен. Уходим мгновенно.
        if (GetLastError() != ERROR_PIPE_BUSY) return INVALID_HANDLE_VALUE;
        if (!WaitNamedPipeW(name.c_str(), 1000)) return INVALID_HANDLE_VALUE;
    }
    return INVALID_HANDLE_VALUE;
}

int main() {
    const std::string input = ReadAllStdin();
    json ev = json::parse(input, nullptr, false);
    if (ev.is_discarded() || !ev.is_object()) return 0;

    HANDLE pipe = ConnectToSprout();
    if (pipe == INVALID_HANDLE_VALUE) return 0;

    json env;
    env["v"] = 1;
    env["pids"] = AncestorPids();
    env["console"] = (unsigned long long)(uintptr_t)GetConsoleWindow();
    env["event"] = ev;
    const std::string msg = env.dump(-1, ' ', false, json::error_handler_t::replace);

    if (!sprout::WriteAll(pipe, msg)) { CloseHandle(pipe); return 0; }

    const std::string eventName = ev.value("hook_event_name", "");
    if (eventName != "PermissionRequest") { CloseHandle(pipe); return 0; }

    std::string replyRaw;
    const bool ok = sprout::ReadPipeMessage(pipe, replyRaw);
    CloseHandle(pipe);
    if (!ok) return 0;

    json reply = json::parse(replyRaw, nullptr, false);
    if (reply.is_discarded() || !reply.is_object()) return 0;
    const std::string behavior = reply.value("behavior", "pass");
    if (behavior != "allow" && behavior != "deny") return 0;   // "pass" -> диалог в терминале

    json decision;
    decision["behavior"] = behavior;
    if (behavior == "deny")
        decision["message"] = reply.value("message", std::string("Denied by the user in Sprout."));
    if (reply.contains("updatedInput")) decision["updatedInput"] = reply["updatedInput"];

    json out;
    out["hookSpecificOutput"]["hookEventName"] = "PermissionRequest";
    out["hookSpecificOutput"]["decision"] = decision;

    HANDLE stdoutH = GetStdHandle(STD_OUTPUT_HANDLE);
    sprout::WriteAll(stdoutH, out.dump(-1, ' ', false, json::error_handler_t::replace));
    return 0;
}
