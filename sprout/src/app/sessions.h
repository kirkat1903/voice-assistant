// Модель сессий Claude Code, собранная из событий хуков.
#pragma once
#include "app.h"
#include <set>

enum class SessStatus { Idle, Working, Waiting, Done };

struct Step {
    std::wstring text;
    std::string toolUseId;
    bool done = false;
    bool failed = false;
};

struct Question {
    std::wstring header, question;
    std::string questionRaw;              // ключ в answers — исходный текст вопроса
    std::vector<std::wstring> labels, descs;
    std::vector<std::string> labelsRaw;
    bool multi = false;
};

// Задача из списка задач Claude Code (TaskCreate/TaskUpdate или TodoWrite).
struct TaskItem {
    std::string id;
    std::wstring subject, active;   // active — «Пишу тесты» (activeForm), пока задача в работе
    int status = 0;                 // 0 — ждёт, 1 — в работе, 2 — готово
};

struct Pending {
    std::shared_ptr<PendingDecision> dec;
    uint64_t id = 0;
    std::string toolName;
    std::wstring title;     // «Claude хочет выполнить команду»
    std::wstring detail;    // сама команда / файл
    json toolInput;
    bool isQuestion = false;
    std::vector<Question> questions;
    size_t currentQ = 0;
    std::vector<std::set<int>> chosen;    // выбранные варианты по вопросам
};

struct Session {
    std::string id;
    std::string cwd;
    std::wstring project;
    SessStatus status = SessStatus::Idle;
    std::deque<Step> steps;
    std::deque<Pending> pendings;
    std::wstring note;          // уведомление / статусная строка
    ULONGLONG started = 0, lastActivity = 0, doneAt = 0;
    std::vector<DWORD> pids;
    HWND console = nullptr;
    int tools = 0;
    std::vector<TaskItem> tasks;
    ULONGLONG turnStart = 0;
    int TasksDone() const { int n = 0; for (auto& t : tasks) n += t.status == 2; return n; }
    const TaskItem* CurrentTask() const {
        for (auto& t : tasks) if (t.status == 1) return &t;
        return nullptr;
    }
};

struct Effects {
    bool ask = false;       // нужен ответ пользователя
    bool done = false;      // агент закончил
    bool started = false;   // новая сессия
    std::string askKind;    // ask / question / waiting — для голоса Айко
    std::wstring project;
    ULONGLONG turnMs = 0;   // сколько длился ход до Stop
};

class SessionStore {
public:
    void Apply(IncomingEvent& ev, ULONGLONG now, Effects& fx);
    void PendingGone(uint64_t id);
    void Cleanup(ULONGLONG now);
    void Remove(const std::string& id);

    std::vector<std::unique_ptr<Session>>& All() { return sessions_; }
    Session* Find(const std::string& id);
    bool AnyPending() const;
    bool AnyWorking() const;
    Session* MostRelevant();     // для однострочного статуса
    ULONGLONG LastDoneAt() const;

    // Ответы на запрос (вызываются из UI)
    static void Allow(Pending& p);
    static void Deny(Pending& p);
    static void PassToTerminal(Pending& p);
    static bool ChooseOption(Pending& p, int option);   // true — все вопросы отвечены и ответ отправлен
    static bool SubmitMulti(Pending& p);

private:
    void ApplyTaskTool(Session& s, const std::string& tool, const json& in, const json& resp);
    bool LoadTasksFromDisk(Session& s);
    Session& Ensure(const json& e, const json& env, ULONGLONG now);
    std::vector<std::unique_ptr<Session>> sessions_;
};

std::wstring DescribeTool(const std::string& name, const json& input);
std::wstring Ellipsize(const std::wstring& s, size_t max);
