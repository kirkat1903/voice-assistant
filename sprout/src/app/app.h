// Общие объявления Sprout.exe
#pragma once
#include "../common/shared.h"
#include <algorithm>
#include <atomic>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include "json.hpp"

using json = nlohmann::json;
using ojson = nlohmann::ordered_json;

// Сообщения окна-островка
#define WM_SPROUT_EVENT        (WM_APP + 1)   // lParam = IncomingEvent*
#define WM_SPROUT_PENDING_GONE (WM_APP + 2)   // lParam = id запроса
#define WM_SPROUT_TRAY         (WM_APP + 3)
#define WM_SPROUT_CHAT_REPLY   (WM_APP + 4)   // lParam = ChatReply*

// Решение по запросу разрешения, которого ждёт поток канала.
struct PendingDecision {
    uint64_t id = 0;
    HANDLE done = nullptr;          // manual-reset event
    std::mutex mu;
    bool finished = false;
    std::string reply;              // JSON-ответ хуку
    ~PendingDecision() { if (done) CloseHandle(done); }

    // Вызывается из UI. false — если решение уже принято (таймаут/обрыв).
    bool Resolve(const std::string& r) {
        std::lock_guard<std::mutex> lock(mu);
        if (finished) return false;
        finished = true;
        reply = r;
        SetEvent(done);
        return true;
    }
};

struct IncomingEvent {
    json envelope;                               // {"v","pids","console","event"}
    std::shared_ptr<PendingDecision> pending;    // только для PermissionRequest
};

// ---- config.cpp ----
struct Config {
    bool sounds = true;
    bool autoExpand = true;
    int permissionTimeoutSec = 90;
    std::string model = "claude-sonnet-5-5";
    int maxTokens = 2048;
    bool firstRunDone = false;
    bool petMode = true;
    int petX = -1, petY = -1;
};
Config& Cfg();
void LoadConfig();
void SaveConfig();
std::wstring AppDataDir();          // %APPDATA%\Sprout
std::wstring LocalAppDataDir();     // %LOCALAPPDATA%\Sprout
std::wstring ExeDir();
bool SaveApiKey(const std::wstring& key);
std::string LoadApiKey();
bool IsAutostartEnabled();
void SetAutostart(bool on);
bool PromptText(HWND owner, const wchar_t* title, const wchar_t* label,
                std::wstring& value, bool password);

// ---- pipe_server.cpp ----
void StartPipeServer(HWND target);

// ---- sound.cpp ----
enum class Sfx { Hello, Ask, Done, Click, Deny };
void InitSounds();
void PlaySfx(Sfx s);

// ---- installer.cpp ----
std::wstring ClaudeSettingsPath();
bool HooksInstalled();
void InstallHooks(HWND owner);
void UninstallHooks(HWND owner);

// ---- chat.cpp ----
void OpenChatWindow(HINSTANCE inst);

// ---- island.cpp ----
HWND CreateIsland(HINSTANCE inst);

// ---- http.cpp ----
struct HttpResult {
    bool ok = false;        // ответ получен (любой HTTP-статус)
    int status = 0;
    std::string body, error;
};
HttpResult HttpPost(const std::wstring& host, int port, bool secure, const std::wstring& path,
                    const std::wstring& headers, const std::string& body, int timeoutMs,
                    const wchar_t* verb = L"POST");

// ---- aiko.cpp: голосовой помощник Айко ----
struct AikoState {
    bool linked = false;
    std::wstring name = L"Айко";
    std::string activity;          // idle / thinking / speaking
    float volume = 0;              // уровень микрофона 0..1
    bool sleeping = false, paused = false, addressed = false;
    std::string mood;              // heard / happy / confused / sad
    double moodAge = 99;           // секунд с последней реакции
    std::wstring status;           // последняя фраза Айко
    ULONGLONG statusAt = 0;        // когда фраза сменилась (GetTickCount64)
};
const AikoState& Aiko();
void AikoPoll(ULONGLONG now);                                  // UI-поток, каждый кадр
bool AikoChat(const std::string& text, std::string& reply);    // false — Айко не запущена
void AikoNotify(const std::string& kind, const std::wstring& project);
bool AikoControl(bool listen);                                 // включить/выключить микрофон Айко

// Команда из канала (голос Айко): обрабатывается в UI-потоке через SendMessage.
#define WM_SPROUT_COMMAND (WM_APP + 5)                         // lParam = CommandCtx*
struct CommandCtx {
    json in;
    std::string out;
};
