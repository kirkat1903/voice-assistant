#include "chats.h"
#include <shellapi.h>
#include <thread>
#include <algorithm>
#include "sqlite/sqlite3.h"

namespace {

std::wstring LocalAppData() {
    wchar_t buf[MAX_PATH * 2] = {};
    if (GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH * 2) == 0) return {};
    return buf;
}

unsigned long long FileTimeNow() {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    return ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
}

std::wstring XmlUnescape(const std::wstring& s) {
    std::wstring out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] != L'&') { out += s[i]; continue; }
        size_t semi = s.find(L';', i);
        if (semi == std::wstring::npos || semi - i > 10) { out += s[i]; continue; }
        std::wstring ent = s.substr(i + 1, semi - i - 1);
        wchar_t c = 0;
        if (ent == L"amp") c = L'&';
        else if (ent == L"lt") c = L'<';
        else if (ent == L"gt") c = L'>';
        else if (ent == L"quot") c = L'"';
        else if (ent == L"apos") c = L'\'';
        else if (!ent.empty() && ent[0] == L'#') {
            unsigned long v = ent.size() > 1 && (ent[1] == L'x' || ent[1] == L'X') ? wcstoul(ent.c_str() + 2, nullptr, 16)
                                                                                   : wcstoul(ent.c_str() + 1, nullptr, 10);
            if (v > 0 && v < 0x10000) c = (wchar_t)v;
        }
        if (c) { out += c; i = semi; } else out += s[i];
    }
    return out;
}

std::wstring Trim(const std::wstring& s) {
    size_t a = s.find_first_not_of(L" \t\r\n");
    if (a == std::wstring::npos) return {};
    size_t b = s.find_last_not_of(L" \t\r\n");
    return s.substr(a, b - a + 1);
}

// <toast><header title="…"/><visual><binding><text>Заголовок</text><text>Текст</text>…
void ParseToast(const std::wstring& xml, RawToast& t) {
    std::vector<std::wstring> texts;
    size_t pos = 0;
    for (;;) {
        size_t a = xml.find(L"<text", pos);
        if (a == std::wstring::npos) break;
        size_t gt = xml.find(L'>', a);
        if (gt == std::wstring::npos) break;
        if (xml[gt - 1] == L'/') { pos = gt + 1; continue; }     // <text/>
        size_t b = xml.find(L"</text>", gt);
        if (b == std::wstring::npos) break;
        texts.push_back(Trim(XmlUnescape(xml.substr(gt + 1, b - gt - 1))));
        pos = b + 7;
    }
    size_t h = xml.find(L"<header");
    if (h != std::wstring::npos) {
        size_t ta = xml.find(L"title=\"", h);
        size_t end = xml.find(L'>', h);
        if (ta != std::wstring::npos && ta < end) {
            size_t tb = xml.find(L'"', ta + 7);
            if (tb != std::wstring::npos) t.header = XmlUnescape(xml.substr(ta + 7, tb - ta - 7));
        }
    }
    if (!texts.empty()) t.title = texts[0];
    for (size_t i = 1; i < texts.size(); ++i) {
        if (texts[i].empty()) continue;
        if (!t.body.empty()) t.body += L" ";
        t.body += texts[i];
    }
    if (t.title.empty()) t.title = t.header;
}

// Один проход: уведомления Claude и Codex из базы Windows.
bool ReadToasts(const std::string& dbPath, std::vector<RawToast>& out) {
    sqlite3* db = nullptr;
    std::string uri = "file:" + dbPath + "?mode=ro";
    if (sqlite3_open_v2(uri.c_str(), &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nullptr) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return false;
    }
    sqlite3_busy_timeout(db, 300);
    const char* sql =
        "SELECT n.Id, n.ArrivalTime, n.Payload, h.PrimaryId FROM Notification n "
        "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
        "WHERE n.Type = 'toast' AND h.PrimaryId LIKE '%OpenAI.Codex%' "
        "ORDER BY n.ArrivalTime";
    sqlite3_stmt* st = nullptr;
    bool ok = sqlite3_prepare_v2(db, sql, -1, &st, nullptr) == SQLITE_OK;
    while (ok) {
        int rc = sqlite3_step(st);
        if (rc == SQLITE_DONE) break;
        if (rc != SQLITE_ROW) { ok = false; break; }
        RawToast t;
        t.id = sqlite3_column_int64(st, 0);
        t.arrival = (unsigned long long)sqlite3_column_int64(st, 1);
        const unsigned char* app = sqlite3_column_text(st, 3);
        t.codex = app && std::string((const char*)app).find("OpenAI.Codex") != std::string::npos;
        const void* blob = sqlite3_column_blob(st, 2);
        int n = sqlite3_column_bytes(st, 2);
        if (blob && n > 0) {
            std::string xml((const char*)blob, n);
            ParseToast(sprout::Widen(xml), t);
        }
        if (!t.title.empty() || !t.body.empty()) out.push_back(std::move(t));
    }
    if (st) sqlite3_finalize(st);
    sqlite3_close(db);
    return ok;
}

void WatchLoop(HWND target) {
    const std::wstring path = LocalAppData() + L"\\Microsoft\\Windows\\Notifications\\wpndatabase.db";
    const std::string p8 = sprout::Narrow(path);
    std::string lastSig;
    for (;;) {
        auto* rows = new std::vector<RawToast>();
        if (ReadToasts(p8, *rows)) {
            // отправляем в UI только когда что-то изменилось
            std::string sig;
            for (auto& r : *rows) sig += std::to_string(r.id) + ",";
            if (sig != lastSig || lastSig.empty()) {
                lastSig = sig.empty() ? "-" : sig;
                if (!PostMessageW(target, WM_SPROUT_TOASTS, 0, (LPARAM)rows)) delete rows;
                rows = nullptr;
            }
        }
        delete rows;
        Sleep(1500);
    }
}

ChatKind Classify(const std::wstring& body) {
    std::wstring b = body;
    if (b.empty()) return ChatKind::Info;
    CharLowerBuffW(&b[0], (DWORD)b.size());
    static const wchar_t* waitWords[] = {L"нужен ваш ответ", L"нужен ответ", L"ваш ответ", L"ждёт", L"ждет",
                                         L"разрешени", L"подтвержд", L"needs your", L"waiting for", L"input",
                                         L"approval", L"permission"};
    for (auto w : waitWords) if (b.find(w) != std::wstring::npos) return ChatKind::Waiting;
    return ChatKind::Done;
}

ChatKind ClassifyCodex(const std::wstring& title, const std::wstring& body) {
    std::wstring text = title + L" " + body;
    CharLowerBuffW(&text[0], (DWORD)text.size());
    for (const auto* word : {L"заверш", L"готов", L"выполн", L"completed", L"finished", L"done"})
        if (text.find(word) != std::wstring::npos) return ChatKind::Done;
    for (const auto* word : {L"ждёт", L"ждет", L"разрешен", L"ответ", L"approval", L"permission", L"input"})
        if (text.find(word) != std::wstring::npos) return ChatKind::Waiting;
    return ChatKind::Info;
}

struct FindApp { HWND found = nullptr; const wchar_t* exe = nullptr; };

BOOL CALLBACK FindAppWindow(HWND h, LPARAM l) {
    auto* f = (FindApp*)l;
    if (!IsWindowVisible(h) && !IsIconic(h)) return TRUE;
    if (GetWindow(h, GW_OWNER) || GetWindowTextLengthW(h) == 0) return TRUE;
    if (GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW) return TRUE;
    DWORD pid = 0;
    GetWindowThreadProcessId(h, &pid);
    HANDLE p = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!p) return TRUE;
    wchar_t exe[MAX_PATH * 2] = {};
    DWORD n = MAX_PATH * 2;
    bool match = false;
    if (QueryFullProcessImageNameW(p, 0, exe, &n)) {
        std::wstring s = exe;
        CharLowerBuffW(&s[0], (DWORD)s.size());
        const std::wstring suffix = std::wstring(L"\\") + f->exe;
        match = s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
    }
    CloseHandle(p);
    if (match) { f->found = h; return FALSE; }
    return TRUE;
}

} // namespace

// Windows не даёт фоновому окну забирать фокус; короткое нажатие Alt снимает этот запрет.
void ForceForeground(HWND w) {
    if (IsIconic(w)) ShowWindow(w, SW_RESTORE);
    keybd_event(VK_MENU, 0, 0, 0);
    keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0);
    SetForegroundWindow(w);
    BringWindowToTop(w);
}

bool FocusClaudeApp() {
    FindApp f{nullptr, L"claude.exe"};
    EnumWindows(FindAppWindow, (LPARAM)&f);
    if (f.found) { ForceForeground(f.found); return true; }
    // приложение закрыто — откроем его по ссылке claude://
    return (INT_PTR)ShellExecuteW(nullptr, L"open", L"claude://", nullptr, nullptr, SW_SHOWNORMAL) > 32;
}

bool FocusCodexApp() {
    FindApp f{nullptr, L"codex.exe"};
    EnumWindows(FindAppWindow, (LPARAM)&f);
    if (f.found) { ForceForeground(f.found); return true; }
    return false;
}

void StartToastWatcher(HWND target) {
    std::thread(WatchLoop, target).detach();
}

ChatItem* ChatStore::Find(const std::wstring& key) {
    for (auto& c : items_) if (c.key == key) return &c;
    return nullptr;
}

bool ChatStore::AnyUnseenWaiting() const {
    for (auto& c : items_) if (!c.seen && c.kind == ChatKind::Waiting) return true;
    return false;
}

const ChatItem* ChatStore::LatestUnseen(ChatKind k) const {
    const ChatItem* best = nullptr;
    for (auto& c : items_)
        if (!c.seen && c.kind == k && (!best || c.arrival > best->arrival)) best = &c;
    return best;
}

void ChatStore::Update(const std::vector<RawToast>& rows, ULONGLONG now, ChatEffects& fx) {
    const unsigned long long ftNow = FileTimeNow();
    const unsigned long long sixHours = 6ULL * 3600 * 10000000ULL;
    std::vector<long long> present;
    std::vector<std::wstring> touched;

    for (const RawToast& r : rows) {
        if (!r.codex) continue;                                             // в интерфейсе показываем только Codex
        present.push_back(r.id);
        const bool isNew = std::find(known_.begin(), known_.end(), r.id) == known_.end();
        if (!isNew) continue;
        known_.push_back(r.id);
        if (ftNow > r.arrival && ftNow - r.arrival > sixHours) continue;    // слишком старое

        std::wstring key = !r.header.empty() ? r.header : r.title;
        if (key.empty()) key = L"Codex";
        key = L"Codex:" + key;
        ChatItem* c = Find(key);
        if (!c) {
            items_.push_back(ChatItem());
            c = &items_.back();
            c->key = key;
        }
        if (c->arrival > r.arrival) continue;                               // пришло что-то новее
        c->title = r.title.empty() ? key : r.title;
        c->text = r.body;
        c->codex = true;
        c->kind = ClassifyCodex(r.title, r.body);
        c->arrival = r.arrival;
        c->at = now - std::min<ULONGLONG>(now, (ftNow > r.arrival ? (ftNow - r.arrival) / 10000ULL : 0));
        c->ids.push_back(r.id);
        // при первом запуске островка старые уведомления показываем, но не объявляем
        c->seen = first_;
        touched.push_back(key);
    }

    // Уведомление исчезло из Центра уведомлений — значит, его открыли или смахнули: считаем увиденным.
    for (auto& c : items_) {
        if (c.seen || c.ids.empty()) continue;
        bool any = false;
        for (long long id : c.ids) if (std::find(present.begin(), present.end(), id) != present.end()) any = true;
        if (!any) c.seen = true;
    }
    known_.erase(std::remove_if(known_.begin(), known_.end(),
                                [&](long long id) { return std::find(present.begin(), present.end(), id) == present.end(); }),
                 known_.end());

    if (!first_) {
        for (const auto& key : touched) {
            const ChatItem* c = Find(key);
            if (!c || c->seen) continue;
            if (c->kind == ChatKind::Waiting) fx.waiting.push_back(key);
            else if (c->kind == ChatKind::Done) fx.done.push_back(key);
        }
    }
    first_ = false;
    std::sort(items_.begin(), items_.end(), [](const ChatItem& a, const ChatItem& b) { return a.arrival > b.arrival; });
}

void ChatStore::Cleanup(ULONGLONG now) {
    items_.erase(std::remove_if(items_.begin(), items_.end(), [&](const ChatItem& c) {
                     ULONGLONG age = now - c.at;
                     if (c.seen && age > 45ULL * 60 * 1000) return true;
                     return age > 6ULL * 3600 * 1000;
                 }),
                 items_.end());
}
