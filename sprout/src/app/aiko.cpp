// Связь с голосовым помощником Айко: её состояние (state.json) -> питомец Sprout,
// чат Sprout -> её бесплатный собеседник, события Claude Code -> её голос.
#include "app.h"
#include <thread>

namespace {

struct Link {
    bool linked = false;
    int port = 0;
    std::string token;
    std::wstring statePath;
    std::wstring name = L"Айко";
    DWORD pid = 0;
};

Link g_link;
std::mutex g_linkMu;          // чат и уведомления читают ссылку из других потоков
AikoState g_state;
ULONGLONG g_linkCheckedAt = 0, g_stateReadAt = 0;
std::wstring g_lastStatus;

double UnixNow() {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER u;
    u.LowPart = ft.dwLowDateTime;
    u.HighPart = ft.dwHighDateTime;
    return (double)(u.QuadPart - 116444736000000000ULL) / 1e7;
}

bool ProcessAlive(DWORD pid) {
    if (!pid) return false;
    HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return GetLastError() == ERROR_ACCESS_DENIED;
    bool alive = WaitForSingleObject(h, 0) == WAIT_TIMEOUT;
    CloseHandle(h);
    return alive;
}

void RefreshLink() {
    Link l;
    std::string text;
    if (sprout::ReadWholeFile(AppDataDir() + L"\\aiko.json", text)) {
        json j = json::parse(text, nullptr, false);
        if (!j.is_discarded() && j.is_object()) {
            l.port = j.value("port", 0);
            l.token = j.value("token", std::string());
            l.statePath = sprout::Widen(j.value("state", std::string()));
            l.pid = j.value("pid", 0u);
            std::string n = j.value("name", std::string());
            if (!n.empty()) l.name = sprout::Widen(n);
            l.linked = l.port > 0 && !l.token.empty() && ProcessAlive(l.pid);
        }
    }
    std::lock_guard<std::mutex> lock(g_linkMu);
    g_link = l;
}

Link CurrentLink() {
    std::lock_guard<std::mutex> lock(g_linkMu);
    return g_link;
}

} // namespace

const AikoState& Aiko() { return g_state; }

void AikoPoll(ULONGLONG now) {
    if (now - g_linkCheckedAt > 2000 || g_linkCheckedAt == 0) {
        g_linkCheckedAt = now;
        RefreshLink();
    }
    Link l = CurrentLink();
    g_state.linked = l.linked;
    g_state.name = l.name;
    if (!l.linked) {
        g_state.activity.clear();
        g_state.volume = 0;
        return;
    }
    if (now - g_stateReadAt < 60) return;
    g_stateReadAt = now;
    std::string text;
    if (!sprout::ReadWholeFile(l.statePath, text)) return;   // файл как раз подменяют — возьмём в следующий раз
    json j = json::parse(text, nullptr, false);
    if (j.is_discarded() || !j.is_object()) return;
    g_state.activity = j.value("activity", std::string("idle"));
    g_state.volume = (float)j.value("volume", 0.0);
    g_state.sleeping = j.value("sleeping", false);
    g_state.paused = j.value("paused", false);
    g_state.addressed = j.value("addressed", false);
    std::string mood = j.contains("mood") && j["mood"].is_string() ? j["mood"].get<std::string>() : "";
    g_state.mood = mood;
    g_state.moodAge = mood.empty() ? 99.0 : UnixNow() - j.value("mood_ts", 0.0);
    std::wstring status = sprout::Widen(j.value("status", std::string()));
    if (status != g_lastStatus) {
        g_lastStatus = status;
        g_state.status = status;
        g_state.statusAt = now;
    }
}

bool AikoChat(const std::string& text, std::string& reply) {
    Link l = CurrentLink();
    if (!l.linked) return false;
    json body = {{"text", text}};
    std::wstring headers = L"Content-Type: application/json\r\nX-Aiko-Token: " + sprout::Widen(l.token) + L"\r\n";
    HttpResult r = HttpPost(L"127.0.0.1", l.port, false, L"/chat", headers,
                            body.dump(-1, ' ', false, json::error_handler_t::replace), 120000);
    if (!r.ok) { reply = "Айко не ответила (" + r.error + "). Она запущена?"; return true; }
    json j = json::parse(r.body, nullptr, false);
    if (r.status != 200 || j.is_discarded()) { reply = "Айко вернула ошибку " + std::to_string(r.status); return true; }
    reply = j.value("text", std::string());
    if (!j.value("ok", false)) reply = "[Айко] " + reply;
    return true;
}

void AikoNotify(const std::string& kind, const std::wstring& project) {
    Link l = CurrentLink();
    if (!l.linked) return;
    json body = {{"kind", kind}, {"project", sprout::Narrow(project)}};
    std::string payload = body.dump(-1, ' ', false, json::error_handler_t::replace);
    std::wstring headers = L"Content-Type: application/json\r\nX-Aiko-Token: " + sprout::Widen(l.token) + L"\r\n";
    int port = l.port;
    std::thread([port, headers, payload] { HttpPost(L"127.0.0.1", port, false, L"/notify", headers, payload, 5000); })
        .detach();
}

bool AikoControl(bool listen) {
    Link l = CurrentLink();
    if (!l.linked) return false;
    json body = {{"action", listen ? "resume" : "pause"}};
    std::wstring headers = L"Content-Type: application/json\r\nX-Aiko-Token: " + sprout::Widen(l.token) + L"\r\n";
    HttpResult r = HttpPost(L"127.0.0.1", l.port, false, L"/control", headers,
                            body.dump(-1, ' ', false, json::error_handler_t::replace), 2000);
    return r.ok && r.status == 200;
}
