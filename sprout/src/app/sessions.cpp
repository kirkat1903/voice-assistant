#include "sessions.h"

namespace {

std::string Str(const json& j, const char* key) {
    if (!j.is_object() || !j.contains(key)) return {};
    const json& v = j[key];
    return v.is_string() ? v.get<std::string>() : std::string();
}

std::wstring BaseName(const std::string& path) {
    std::wstring w = sprout::Widen(path);
    while (!w.empty() && (w.back() == L'\\' || w.back() == L'/')) w.pop_back();
    size_t pos = w.find_last_of(L"\\/");
    return pos == std::wstring::npos ? w : w.substr(pos + 1);
}

std::wstring FirstLine(const std::string& s) {
    std::wstring w = sprout::Widen(s);
    size_t pos = w.find_first_of(L"\r\n");
    std::wstring line = pos == std::wstring::npos ? w : w.substr(0, pos) + L" …";
    size_t a = line.find_first_not_of(L" \t");
    return a == std::wstring::npos ? L"" : line.substr(a);
}

std::wstring Host(const std::string& url) {
    std::string u = url;
    size_t p = u.find("://");
    if (p != std::string::npos) u = u.substr(p + 3);
    p = u.find('/');
    if (p != std::string::npos) u = u.substr(0, p);
    return sprout::Widen(u);
}

const size_t kMaxSteps = 6;

} // namespace

std::wstring Ellipsize(const std::wstring& s, size_t max) {
    if (s.size() <= max) return s;
    return s.substr(0, max > 1 ? max - 1 : 0) + L"…";
}

std::wstring DescribeTool(const std::string& name, const json& in) {
    if (name == "Bash" || name == "PowerShell") return L"$ " + Ellipsize(FirstLine(Str(in, "command")), 90);
    if (name == "Read") return L"Читает " + BaseName(Str(in, "file_path"));
    if (name == "Write") return L"Создаёт " + BaseName(Str(in, "file_path"));
    if (name == "Edit" || name == "MultiEdit") return L"Правит " + BaseName(Str(in, "file_path"));
    if (name == "NotebookEdit") return L"Правит " + BaseName(Str(in, "notebook_path"));
    if (name == "Grep") return L"Ищет «" + Ellipsize(sprout::Widen(Str(in, "pattern")), 40) + L"»";
    if (name == "Glob") return L"Ищет файлы " + Ellipsize(sprout::Widen(Str(in, "pattern")), 40);
    if (name == "WebFetch") return L"Открывает " + Host(Str(in, "url"));
    if (name == "WebSearch") return L"Ищет в сети «" + Ellipsize(sprout::Widen(Str(in, "query")), 40) + L"»";
    if (name == "Task" || name == "Agent") return L"Субагент: " + Ellipsize(sprout::Widen(Str(in, "description")), 60);
    if (name == "TodoWrite" || name == "TaskCreate" || name == "TaskUpdate") return L"Обновляет список задач";
    if (name == "AskUserQuestion") return L"Задаёт вопрос";
    if (name.rfind("mcp__", 0) == 0) {
        std::string rest = name.substr(5);
        size_t p = rest.find("__");
        if (p != std::string::npos) return sprout::Widen(rest.substr(0, p)) + L" · " + sprout::Widen(rest.substr(p + 2));
        return sprout::Widen(rest);
    }
    return sprout::Widen(name);
}

Session* SessionStore::Find(const std::string& id) {
    for (auto& s : sessions_) if (s->id == id) return s.get();
    return nullptr;
}

Session& SessionStore::Ensure(const json& e, const json& env, ULONGLONG now) {
    std::string id = Str(e, "session_id");
    if (id.empty()) id = "unknown";
    Session* s = Find(id);
    if (!s) {
        auto ns = std::make_unique<Session>();
        ns->id = id;
        ns->started = now;
        s = ns.get();
        sessions_.push_back(std::move(ns));
    }
    std::string cwd = Str(e, "cwd");
    if (!cwd.empty()) { s->cwd = cwd; s->project = BaseName(cwd); }
    if (s->project.empty()) s->project = L"Задача";
    if (env.contains("pids") && env["pids"].is_array()) {
        s->pids.clear();
        for (const auto& p : env["pids"]) if (p.is_number_unsigned()) s->pids.push_back(p.get<DWORD>());
    }
    if (env.contains("console") && env["console"].is_number_unsigned())
        s->console = (HWND)(uintptr_t)env["console"].get<unsigned long long>();
    s->lastActivity = now;
    return *s;
}

// ---------- задачи ----------
namespace {
int TaskStatus(const std::string& s) {
    if (s == "completed" || s == "done") return 2;
    if (s == "in_progress") return 1;
    return 0;
}

std::wstring TasksDir(const std::string& sessionId) {
    wchar_t env[MAX_PATH * 2] = {};
    std::wstring base;
    if (GetEnvironmentVariableW(L"CLAUDE_CONFIG_DIR", env, MAX_PATH * 2) > 0) base = env;
    else if (GetEnvironmentVariableW(L"USERPROFILE", env, MAX_PATH * 2) > 0) base = std::wstring(env) + L"\\.claude";
    else return {};
    return base + L"\\tasks\\" + sprout::Widen(sessionId);
}

long long NumId(const std::string& id) {
    if (id.empty() || id.size() > 12) return -1;
    for (char c : id) if (c < '0' || c > '9') return -1;
    return std::stoll(id);
}

void SortTasks(std::vector<TaskItem>& v) {
    std::stable_sort(v.begin(), v.end(), [](const TaskItem& a, const TaskItem& b) {
        long long x = NumId(a.id), y = NumId(b.id);
        if (x >= 0 && y >= 0) return x < y;
        return a.id < b.id;
    });
}

// id новой задачи из ответа TaskCreate: {"task":{"id":"3"}}, {"id":3} или текст «Task #3 created».
std::string IdFromResponse(const json& r) {
    if (r.is_object()) {
        for (const char* k : {"id", "taskId"}) {
            if (r.contains(k)) {
                if (r[k].is_string()) return r[k].get<std::string>();
                if (r[k].is_number_integer()) return std::to_string(r[k].get<long long>());
            }
        }
        if (r.contains("task")) return IdFromResponse(r["task"]);
    }
    std::string text = r.is_string() ? r.get<std::string>() : r.dump(-1, ' ', false, json::error_handler_t::replace);
    size_t p = text.find('#');
    if (p != std::string::npos) {
        size_t e = p + 1;
        while (e < text.size() && text[e] >= '0' && text[e] <= '9') ++e;
        if (e > p + 1) return text.substr(p + 1, e - p - 1);
    }
    return {};
}
} // namespace

bool SessionStore::LoadTasksFromDisk(Session& s) {
    std::wstring dir = TasksDir(s.id);
    if (dir.empty()) return false;
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW((dir + L"\\*.json").c_str(), &fd);
    if (f == INVALID_HANDLE_VALUE) return false;
    std::vector<TaskItem> items;
    do {
        std::string text;
        if (!sprout::ReadWholeFile(dir + L"\\" + fd.cFileName, text)) continue;
        json j = json::parse(text, nullptr, false);
        if (j.is_discarded() || !j.is_object()) continue;
        TaskItem t;
        if (j.contains("id")) t.id = j["id"].is_string() ? j["id"].get<std::string>() : j["id"].dump();
        t.subject = sprout::Widen(Str(j, "subject"));
        if (t.subject.empty()) t.subject = sprout::Widen(Str(j, "content"));
        t.active = sprout::Widen(Str(j, "activeForm"));
        std::string st = Str(j, "status");
        if (st == "deleted" || t.subject.empty()) continue;
        t.status = TaskStatus(st);
        items.push_back(std::move(t));
    } while (FindNextFileW(f, &fd));
    FindClose(f);
    if (items.empty()) return false;
    SortTasks(items);
    s.tasks = std::move(items);
    return true;
}

void SessionStore::ApplyTaskTool(Session& s, const std::string& tool, const json& in, const json& resp) {
    if (tool == "TodoWrite") {
        if (!in.contains("todos") || !in["todos"].is_array()) return;
        std::vector<TaskItem> items;
        int i = 0;
        for (const auto& t : in["todos"]) {
            TaskItem it;
            it.id = std::to_string(++i);
            it.subject = sprout::Widen(Str(t, "content"));
            it.active = sprout::Widen(Str(t, "activeForm"));
            it.status = TaskStatus(Str(t, "status"));
            if (!it.subject.empty()) items.push_back(std::move(it));
        }
        s.tasks = std::move(items);
        return;
    }
    if (tool != "TaskCreate" && tool != "TaskUpdate") return;
    if (LoadTasksFromDisk(s)) return;        // если Claude Code хранит задачи на диске — это самый точный источник

    if (tool == "TaskCreate") {
        TaskItem t;
        t.id = IdFromResponse(resp);
        if (t.id.empty()) {
            long long mx = 0;
            for (auto& x : s.tasks) mx = std::max(mx, NumId(x.id));
            t.id = std::to_string(mx + 1);
        }
        t.subject = sprout::Widen(Str(in, "subject"));
        t.active = sprout::Widen(Str(in, "activeForm"));
        if (t.subject.empty()) return;
        for (auto& x : s.tasks) if (x.id == t.id) { x = t; return; }
        s.tasks.push_back(std::move(t));
        SortTasks(s.tasks);
        return;
    }
    // TaskUpdate
    std::string id;
    if (in.contains("taskId")) id = in["taskId"].is_string() ? in["taskId"].get<std::string>() : in["taskId"].dump();
    if (id.empty()) return;
    auto it = std::find_if(s.tasks.begin(), s.tasks.end(), [&](const TaskItem& t) { return t.id == id; });
    std::string st = Str(in, "status");
    if (st == "deleted") {
        if (it != s.tasks.end()) s.tasks.erase(it);
        return;
    }
    if (it == s.tasks.end()) {
        TaskItem t;
        t.id = id;
        t.subject = L"Задача #" + sprout::Widen(id);
        s.tasks.push_back(t);
        SortTasks(s.tasks);
        it = std::find_if(s.tasks.begin(), s.tasks.end(), [&](const TaskItem& x) { return x.id == id; });
    }
    if (!st.empty()) it->status = TaskStatus(st);
    std::wstring subj = sprout::Widen(Str(in, "subject"));
    if (!subj.empty()) it->subject = subj;
    std::wstring act = sprout::Widen(Str(in, "activeForm"));
    if (!act.empty()) it->active = act;
}

void SessionStore::Apply(IncomingEvent& ev, ULONGLONG now, Effects& fx) {
    const json& env = ev.envelope;
    const json& e = env["event"];
    const std::string name = Str(e, "hook_event_name");

    if (name == "SessionEnd") { Remove(Str(e, "session_id")); return; }

    const bool isNew = !Find(Str(e, "session_id").empty() ? "unknown" : Str(e, "session_id"));
    Session& s = Ensure(e, env, now);
    if (isNew) { fx.started = true; LoadTasksFromDisk(s); }
    fx.project = s.project;

    auto pushStep = [&](std::wstring text, const std::string& toolUseId) {
        Step st;
        st.text = std::move(text);
        st.toolUseId = toolUseId;
        s.steps.push_back(st);
        while (s.steps.size() > kMaxSteps) s.steps.pop_front();
    };

    if (name == "SessionStart") {
        s.status = SessStatus::Idle;
        s.note = L"Сессия запущена";
    } else if (name == "UserPromptSubmit") {
        s.status = SessStatus::Working;
        s.note.clear();
        s.doneAt = 0;
        s.turnStart = now;
        pushStep(L"Запрос: " + Ellipsize(FirstLine(Str(e, "prompt")), 70), "");
        s.steps.back().done = true;   // запрос — не действие, ждать его завершения не нужно
    } else if (name == "PreToolUse") {
        s.status = s.pendings.empty() ? SessStatus::Working : SessStatus::Waiting;
        s.note.clear();
        const std::string tool = Str(e, "tool_name");
        const json in = e.contains("tool_input") ? e["tool_input"] : json::object();
        pushStep(DescribeTool(tool, in), Str(e, "tool_use_id"));
    } else if (name == "PostToolUse" || name == "PostToolUseFailure") {
        const std::string tuid = Str(e, "tool_use_id");
        for (auto it = s.steps.rbegin(); it != s.steps.rend(); ++it) {
            if (!it->done && (tuid.empty() || it->toolUseId == tuid)) {
                it->done = true;
                it->failed = (name == "PostToolUseFailure");
                break;
            }
        }
        ++s.tools;
        if (s.pendings.empty()) s.status = SessStatus::Working;
        const json in = e.contains("tool_input") ? e["tool_input"] : json::object();
        const json resp = e.contains("tool_response") ? e["tool_response"] : json();
        ApplyTaskTool(s, Str(e, "tool_name"), in, resp);
    } else if (name == "PermissionRequest") {
        Pending p;
        p.dec = ev.pending;
        p.id = ev.pending ? ev.pending->id : 0;
        p.toolName = Str(e, "tool_name");
        p.toolInput = e.contains("tool_input") ? e["tool_input"] : json::object();
        if (p.toolName == "AskUserQuestion" && p.toolInput.contains("questions") && p.toolInput["questions"].is_array()) {
            p.isQuestion = true;
            for (const auto& q : p.toolInput["questions"]) {
                Question qq;
                qq.questionRaw = Str(q, "question");
                qq.question = sprout::Widen(qq.questionRaw);
                qq.header = sprout::Widen(Str(q, "header"));
                qq.multi = q.value("multiSelect", false);
                if (q.contains("options") && q["options"].is_array()) {
                    for (const auto& o : q["options"]) {
                        std::string label = Str(o, "label");
                        qq.labelsRaw.push_back(label);
                        qq.labels.push_back(sprout::Widen(label));
                        qq.descs.push_back(sprout::Widen(Str(o, "description")));
                    }
                }
                p.questions.push_back(qq);
            }
            p.chosen.resize(p.questions.size());
            p.title = L"Агент спрашивает";
            if (p.questions.empty()) p.isQuestion = false;
        }
        if (!p.isQuestion) {
            const std::string t = p.toolName;
            if (t == "Bash" || t == "PowerShell") {
                p.title = L"Выполнить команду?";
                p.detail = sprout::Widen(Str(p.toolInput, "command"));
            } else if (t == "Write" || t == "Edit" || t == "MultiEdit") {
                p.title = t == "Write" ? L"Создать файл?" : L"Изменить файл?";
                p.detail = sprout::Widen(Str(p.toolInput, "file_path"));
            } else if (t == "WebFetch") {
                p.title = L"Открыть страницу?";
                p.detail = sprout::Widen(Str(p.toolInput, "url"));
            } else {
                p.title = L"Разрешить " + DescribeTool(t, p.toolInput) + L"?";
                std::string dump = p.toolInput.dump(-1, ' ', false, json::error_handler_t::replace);
                p.detail = sprout::Widen(dump);
            }
            p.detail = Ellipsize(p.detail, 400);
        }
        fx.askKind = p.isQuestion ? "question" : "ask";
        s.pendings.push_back(std::move(p));
        s.status = SessStatus::Waiting;
        fx.ask = true;
    } else if (name == "Notification") {
        const std::string type = Str(e, "notification_type");
        const std::wstring msg = Ellipsize(FirstLine(Str(e, "message")), 90);
        if (type == "idle_prompt" && s.status == SessStatus::Done) {
            s.note = L"Готово · ждёт следующего запроса";   // напоминание после Stop — без повторного звонка
        } else if (type == "idle_prompt" || type == "agent_needs_input") {
            s.status = SessStatus::Waiting;
            s.note = L"Ждёт твоего ответа в терминале";
            fx.ask = true;
            fx.askKind = "waiting";
        } else if (type == "permission_prompt") {
            if (s.pendings.empty()) {
                s.status = SessStatus::Waiting;
                s.note = L"Ждёт разрешения в терминале";
                fx.ask = true;
                fx.askKind = "ask";
            }
        } else if (!msg.empty()) {
            s.note = msg;
        }
    } else if (name == "Stop") {
        for (auto& st : s.steps) st.done = true;
        s.status = SessStatus::Done;
        s.doneAt = now;
        s.note = L"Готово";
        fx.done = true;
        fx.turnMs = s.turnStart ? now - s.turnStart : 0;
        LoadTasksFromDisk(s);
    } else if (name == "SubagentStop") {
        pushStep(L"Субагент закончил", "");
        s.steps.back().done = true;
    }
}

void SessionStore::PendingGone(uint64_t id) {
    for (auto& s : sessions_) {
        for (auto it = s->pendings.begin(); it != s->pendings.end(); ++it) {
            if (it->id == id) {
                s->pendings.erase(it);
                if (s->pendings.empty() && s->status == SessStatus::Waiting) s->status = SessStatus::Working;
                return;
            }
        }
    }
}

void SessionStore::Remove(const std::string& id) {
    sessions_.erase(std::remove_if(sessions_.begin(), sessions_.end(),
                                   [&](const std::unique_ptr<Session>& s) {
                                       if (s->id != id) return false;
                                       for (auto& p : s->pendings) PassToTerminal(p);
                                       return true;
                                   }),
                    sessions_.end());
}

void SessionStore::Cleanup(ULONGLONG now) {
    const ULONGLONG doneTtl = 20ULL * 60 * 1000, idleTtl = 60ULL * 60 * 1000;
    sessions_.erase(std::remove_if(sessions_.begin(), sessions_.end(),
                                   [&](const std::unique_ptr<Session>& s) {
                                       if (!s->pendings.empty()) return false;
                                       if (s->status == SessStatus::Done && now - s->doneAt > doneTtl) return true;
                                       return now - s->lastActivity > idleTtl;
                                   }),
                    sessions_.end());
}

bool SessionStore::AnyPending() const {
    for (auto& s : sessions_) if (!s->pendings.empty()) return true;
    return false;
}

bool SessionStore::AnyWorking() const {
    for (auto& s : sessions_) if (s->status == SessStatus::Working) return true;
    return false;
}

ULONGLONG SessionStore::LastDoneAt() const {
    ULONGLONG t = 0;
    for (auto& s : sessions_) t = std::max(t, s->doneAt);
    return t;
}

Session* SessionStore::MostRelevant() {
    Session* best = nullptr;
    auto rank = [](const Session* s) {
        if (!s->pendings.empty() || s->status == SessStatus::Waiting) return 3;
        if (s->status == SessStatus::Working) return 2;
        if (s->status == SessStatus::Done) return 1;
        return 0;
    };
    for (auto& s : sessions_) {
        if (!best || rank(s.get()) > rank(best) ||
            (rank(s.get()) == rank(best) && s->lastActivity > best->lastActivity))
            best = s.get();
    }
    return best;
}

void SessionStore::Allow(Pending& p) {
    if (p.dec) p.dec->Resolve(R"({"behavior":"allow"})");
}

void SessionStore::Deny(Pending& p) {
    if (p.dec) p.dec->Resolve(R"({"behavior":"deny","message":"The user denied this action from Sprout."})");
}

void SessionStore::PassToTerminal(Pending& p) {
    if (p.dec) p.dec->Resolve(R"({"behavior":"pass"})");
}

namespace {
bool SendAnswers(Pending& p) {
    json answers = json::object();
    for (size_t i = 0; i < p.questions.size(); ++i) {
        std::string joined;
        for (int idx : p.chosen[i]) {
            if (idx < 0 || idx >= (int)p.questions[i].labelsRaw.size()) continue;
            if (!joined.empty()) joined += ", ";
            joined += p.questions[i].labelsRaw[idx];
        }
        answers[p.questions[i].questionRaw] = joined;
    }
    json updated = p.toolInput;
    updated["answers"] = answers;
    json reply;
    reply["behavior"] = "allow";
    reply["updatedInput"] = updated;
    return p.dec && p.dec->Resolve(reply.dump(-1, ' ', false, json::error_handler_t::replace));
}
} // namespace

bool SessionStore::ChooseOption(Pending& p, int option) {
    if (!p.isQuestion || p.currentQ >= p.questions.size()) return false;
    Question& q = p.questions[p.currentQ];
    if (q.multi) {
        auto& set = p.chosen[p.currentQ];
        if (set.count(option)) set.erase(option); else set.insert(option);
        return false;
    }
    p.chosen[p.currentQ] = {option};
    ++p.currentQ;
    if (p.currentQ >= p.questions.size()) return SendAnswers(p);
    return false;
}

bool SessionStore::SubmitMulti(Pending& p) {
    if (!p.isQuestion || p.currentQ >= p.questions.size()) return false;
    if (p.chosen[p.currentQ].empty()) return false;
    ++p.currentQ;
    if (p.currentQ >= p.questions.size()) return SendAnswers(p);
    return false;
}
