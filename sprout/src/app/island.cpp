// Островок вверху экрана: прячется, когда ничего не происходит, выглядывает при
// наведении, раскрывается в панель сессий с кнопками «Разрешить / Отклонить».
#include "app.h"
#include "gdiplus_inc.h"
#include "pet.h"
#include "sessions.h"
#include "chats.h"
#include <shellapi.h>
#include <windowsx.h>
#include <cmath>
#include <random>

using namespace Gdiplus;

namespace {

// ---------- размеры в логических пикселях (96 dpi) ----------
const float WIN_W = 460, WIN_H = 660;
const float PET_W = 360, PET_H = 180;
const float PANEL_W = 420, PILL_W = 260, PILL_H = 38, PEEK_W = 140, PEEK_H = 6;
const float CARD_PAD = 12;

enum class Vis { Hidden, Pill, Panel };

enum : UINT {
    TIMER_FRAME = 1, TIMER_DEMO = 2,
    ID_CHAT = 100, ID_INSTALL, ID_UNINSTALL, ID_APIKEY, ID_SOUNDS, ID_AUTOEXPAND,
    ID_AUTOSTART, ID_DEMO, ID_HIDE, ID_OPENCFG, ID_QUIT, ID_PET_MODE, ID_AIKO_LISTEN,
};

// ---------- цвета ----------
const Color C_BG(250, 16, 16, 18);
const Color C_CARD(255, 30, 30, 34);
const Color C_TEXT(255, 238, 238, 242);
const Color C_DIM(255, 150, 150, 160);
const Color C_GREEN(255, 96, 214, 142);
const Color C_AMBER(255, 255, 190, 70);
const Color C_RED(255, 255, 110, 100);
const Color C_BLUE(255, 120, 170, 255);
const Color C_BTN(255, 56, 56, 62);
const Color C_BTN_OK(255, 40, 150, 86);
const Color C_BTN_NO(255, 150, 54, 54);

struct Hit {
    RectF r;
    std::function<void()> fn;
};

struct State {
    HWND hwnd = nullptr;
    HINSTANCE inst = nullptr;
    float scale = 1;
    int winX = 0, winY = 0, wpx = 0, hpx = 0;
    HDC memDC = nullptr;
    HBITMAP dib = nullptr, oldBmp = nullptr;
    void* bits = nullptr;

    SessionStore store;
    ChatStore chats;                 // чаты приложения Claude (по уведомлениям Windows)
    Vis vis = Vis::Hidden;
    float curW = PEEK_W, curH = PEEK_H, content = 0;
    float panelH = 140;
    ULONGLONG t0 = 0, lastFrame = 0, lastActiveAt = 0, lastAskSound = 0, lastCleanup = 0, lastFsCheck = 0, lastTopmostAt = 0;
    bool hovered = false;
    ULONGLONG hoverSince = 0, leaveSince = 0;
    PointF cursor{-1000, -1000};
    UINT timerMs = 0;
    bool needsRedraw = true;

    PetAnim pet;
    double nextBlink = 2.0, blinkStart = -1, hopStart = -10, happyUntil = 0;
    ULONGLONG voiceHighAt = 0, voiceQuietAt = 0, surprisedUntil = 0, thinkingUntil = 0;
    bool voiceArmed = true;
    std::mt19937 rng{12345};

    std::vector<Hit> hits;
    int pressed = -1;
    POINT dragAt{}, dragOrigin{};
    bool dragged = false;

    bool userHidden = false, fsHidden = false, shown = false;
    bool hooksInstalledCache = false;
    ULONGLONG hooksCheckedAt = 0;

    NOTIFYICONDATAW nid{};
    UINT taskbarCreated = 0;
    HICON icon = nullptr;

    std::unique_ptr<FontFamily> ffUi, ffMono;
    std::unique_ptr<Font> fTitle, fBold, fText, fSmall, fMono;

    int demoStep = -1;
};
State G;

double Secs() { return (GetTickCount64() - G.t0) / 1000.0; }

// ---------- текст ----------
void DrawText1(Graphics& g, const std::wstring& s, Font* f, const Color& c, const RectF& rc,
               StringAlignment align = StringAlignmentNear) {
    StringFormat fmt;
    fmt.SetFormatFlags(StringFormatFlagsNoWrap);
    fmt.SetTrimming(StringTrimmingEllipsisCharacter);
    fmt.SetAlignment(align);
    fmt.SetLineAlignment(StringAlignmentCenter);
    SolidBrush b(c);
    g.DrawString(s.c_str(), (INT)s.size(), f, rc, &fmt, &b);
}

float MeasureWrapped(Graphics& g, const std::wstring& s, Font* f, float width) {
    if (s.empty()) return 0;
    RectF box;
    g.MeasureString(s.c_str(), (INT)s.size(), f, RectF(0, 0, width, 10000), nullptr, &box);
    return box.Height;
}

void DrawWrapped(Graphics& g, const std::wstring& s, Font* f, const Color& c, const RectF& rc) {
    StringFormat fmt;
    fmt.SetTrimming(StringTrimmingEllipsisWord);
    fmt.SetFormatFlags(StringFormatFlagsLineLimit);
    SolidBrush b(c);
    g.DrawString(s.c_str(), (INT)s.size(), f, rc, &fmt, &b);
}

float MeasureWidth(Graphics& g, const std::wstring& s, Font* f) {
    RectF box;
    g.MeasureString(s.c_str(), (INT)s.size(), f, PointF(0, 0), &box);
    return box.Width;
}

void RoundRect(GraphicsPath& p, const RectF& r, float rad) {
    rad = std::min(rad, std::min(r.Width, r.Height) / 2);
    float d = rad * 2;
    if (d <= 0.5f) { p.AddRectangle(r); return; }
    p.AddArc(r.X, r.Y, d, d, 180, 90);
    p.AddArc(r.X + r.Width - d, r.Y, d, d, 270, 90);
    p.AddArc(r.X + r.Width - d, r.Y + r.Height - d, d, d, 0, 90);
    p.AddArc(r.X, r.Y + r.Height - d, d, d, 90, 90);
    p.CloseFigure();
}

void FillRound(Graphics& g, const RectF& r, float rad, const Color& c) {
    GraphicsPath p;
    RoundRect(p, r, rad);
    SolidBrush b(c);
    g.FillPath(&b, &p);
}

void CloudBubblePath(GraphicsPath& p, const RectF& r) {
    const float x = r.X, y = r.Y, w = r.Width, h = r.Height;
    p.StartFigure();
    p.AddBezier(PointF(x + w * .08f, y + h * .58f),
                PointF(x - w * .01f, y + h * .47f), PointF(x + w * .02f, y + h * .27f),
                PointF(x + w * .17f, y + h * .25f));
    p.AddBezier(PointF(x + w * .17f, y + h * .25f),
                PointF(x + w * .12f, y + h * .08f), PointF(x + w * .29f, y + h * .02f),
                PointF(x + w * .36f, y + h * .14f));
    p.AddBezier(PointF(x + w * .36f, y + h * .14f),
                PointF(x + w * .43f, y - h * .05f), PointF(x + w * .61f, y - h * .05f),
                PointF(x + w * .66f, y + h * .13f));
    p.AddBezier(PointF(x + w * .66f, y + h * .13f),
                PointF(x + w * .79f, y + h * .02f), PointF(x + w * .92f, y + h * .13f),
                PointF(x + w * .90f, y + h * .29f));
    p.AddBezier(PointF(x + w * .90f, y + h * .29f),
                PointF(x + w * 1.04f, y + h * .27f), PointF(x + w * 1.07f, y + h * .48f),
                PointF(x + w * .94f, y + h * .56f));
    p.AddBezier(PointF(x + w * .94f, y + h * .56f),
                PointF(x + w * 1.03f, y + h * .70f), PointF(x + w * .93f, y + h * .84f),
                PointF(x + w * .82f, y + h * .79f));
    p.AddBezier(PointF(x + w * .82f, y + h * .79f),
                PointF(x + w * .80f, y + h * .98f), PointF(x + w * .65f, y + h * 1.02f),
                PointF(x + w * .58f, y + h * .87f));
    p.AddBezier(PointF(x + w * .58f, y + h * .87f),
                PointF(x + w * .50f, y + h * 1.04f), PointF(x + w * .34f, y + h * 1.01f),
                PointF(x + w * .31f, y + h * .85f));
    // Хвостик направлен к Ростку.
    p.AddLine(PointF(x + w * .31f, y + h * .85f), PointF(x - 9, y + h + 20));
    p.AddLine(PointF(x - 9, y + h + 20), PointF(x + w * .10f, y + h * .70f));
    p.AddBezier(PointF(x + w * .10f, y + h * .70f),
                PointF(x - w * .01f, y + h * .77f), PointF(x - w * .03f, y + h * .62f),
                PointF(x + w * .08f, y + h * .58f));
    p.CloseFigure();
}

void DrawCloudBubble(Graphics& g, const RectF& r) {
    GraphicsPath cloud;
    CloudBubblePath(cloud, r);
    GraphicsState saved = g.Save();
    g.TranslateTransform(5, 7);
    SolidBrush shadow(Color(65, 20, 30, 25));
    g.FillPath(&shadow, &cloud);
    g.Restore(saved);
    SolidBrush white(Color(252, 255, 255, 255));
    Pen outline(Color(255, 35, 38, 36), 3.0f);
    outline.SetLineJoin(LineJoinRound);
    g.FillPath(&white, &cloud);
    g.DrawPath(&outline, &cloud);
}

Color Lighten(const Color& c, int d) {
    auto cl = [](int v) { return (BYTE)std::max(0, std::min(255, v)); };
    return Color(c.GetA(), cl(c.GetR() + d), cl(c.GetG() + d), cl(c.GetB() + d));
}

bool Hovering(const RectF& r) { return r.Contains(G.cursor); }

// Кнопка-таблетка. Возвращает ширину.
float Button(Graphics& g, bool draw, float x, float y, float h, const std::wstring& label, const Color& bg,
             std::function<void()> fn, float fixedW = 0) {
    float w = fixedW > 0 ? fixedW : MeasureWidth(g, label, G.fBold.get()) + 22;
    RectF r(x, y, w, h);
    if (draw) {
        FillRound(g, r, h / 2, Hovering(r) ? Lighten(bg, 22) : bg);
        DrawText1(g, label, G.fBold.get(), C_TEXT, r, StringAlignmentCenter);
        G.hits.push_back({r, std::move(fn)});
    }
    return w;
}

std::wstring Elapsed(ULONGLONG ms) {
    ULONGLONG m = ms / 60000;
    if (m < 1) return L"сейчас";
    if (m < 60) return std::to_wstring(m) + L" мин";
    return std::to_wstring(m / 60) + L" ч " + std::to_wstring(m % 60) + L" мин";
}

// ---------- переход к окну терминала ----------
struct FindCtx { DWORD pid; HWND found; };

BOOL CALLBACK FindWindowForPid(HWND h, LPARAM l) {
    auto* c = (FindCtx*)l;
    if (!IsWindowVisible(h) || GetWindow(h, GW_OWNER) || GetWindowTextLengthW(h) == 0) return TRUE;
    DWORD pid = 0;
    GetWindowThreadProcessId(h, &pid);
    if (pid == c->pid) { c->found = h; return FALSE; }
    return TRUE;
}

void FocusSession(const std::string& sid) {
    Session* s = G.store.Find(sid);
    if (!s) return;
    HWND target = nullptr;
    if (s->console && IsWindow(s->console) && IsWindowVisible(s->console)) target = s->console;
    for (DWORD pid : s->pids) {
        if (target) break;
        FindCtx c{pid, nullptr};
        EnumWindows(FindWindowForPid, (LPARAM)&c);
        target = c.found;
    }
    if (!target) { PlaySfx(Sfx::Deny); return; }
    ForceForeground(target);
}

// ---------- действия с запросами ----------
Pending* FindPending(const std::string& sid, uint64_t id) {
    Session* s = G.store.Find(sid);
    if (!s) return nullptr;
    for (auto& p : s->pendings) if (p.id == id) return &p;
    return nullptr;
}

void ResolvedInUi(uint64_t id) {
    G.store.PendingGone(id);
    G.needsRedraw = true;
}

void DoAllow(const std::string& sid, uint64_t id) {
    if (Pending* p = FindPending(sid, id)) { SessionStore::Allow(*p); PlaySfx(Sfx::Click); ResolvedInUi(id); }
}
void DoDeny(const std::string& sid, uint64_t id) {
    if (Pending* p = FindPending(sid, id)) { SessionStore::Deny(*p); PlaySfx(Sfx::Deny); ResolvedInUi(id); }
}
void DoTerminal(const std::string& sid, uint64_t id) {
    if (Pending* p = FindPending(sid, id)) { SessionStore::PassToTerminal(*p); ResolvedInUi(id); }
    FocusSession(sid);
}
void DoChoose(const std::string& sid, uint64_t id, int opt) {
    if (Pending* p = FindPending(sid, id)) {
        PlaySfx(Sfx::Click);
        if (SessionStore::ChooseOption(*p, opt)) ResolvedInUi(id);
    }
}
void DoSubmitMulti(const std::string& sid, uint64_t id) {
    if (Pending* p = FindPending(sid, id)) {
        if (SessionStore::SubmitMulti(*p)) { PlaySfx(Sfx::Click); ResolvedInUi(id); }
    }
}

// ---------- голосовые команды от Айко ----------
std::wstring AikoPhrase() {
    const AikoState& ak = Aiko();
    if (ak.sleeping) return L"спит";
    if (ak.paused) return L"на паузе";
    if (ak.activity == "speaking") return L"говорит";
    if (ak.activity == "thinking") return L"думает";
    if (ak.volume > 0.15f) return L"слышит тебя";
    return L"слушает";
}

std::wstring LowerFirst(std::wstring s) {
    if (!s.empty()) CharLowerBuffW(&s[0], 1);
    while (!s.empty() && (s.back() == L'?' || s.back() == L' ')) s.pop_back();
    return s;
}

std::wstring ProjectWhere(const Session& s) { return L"в проекте " + s.project; }

std::wstring SessionSentence(const Session& s) {
    if (!s.pendings.empty()) {
        const Pending& p = s.pendings.front();
        if (p.isQuestion && !p.questions.empty())
            return s.project + L": нужен ответ — " + p.questions[std::min(p.currentQ, p.questions.size() - 1)].question;
        return s.project + L": ждёт твоего решения — " + LowerFirst(p.title) + L".";
    }
    std::wstring out = s.project + L": ";
    const int total = (int)s.tasks.size(), done = s.TasksDone();
    switch (s.status) {
    case SessStatus::Working: {
        out += L"работает";
        if (const TaskItem* cur = s.CurrentTask()) out += L", сейчас — " + LowerFirst(cur->active.empty() ? cur->subject : cur->active);
        else if (!s.steps.empty() && s.steps.back().text.rfind(L"$ ", 0) == 0) out += L", выполняет команду";
        else if (!s.steps.empty()) out += L", сейчас — " + LowerFirst(s.steps.back().text);
        if (total) out += L"; готово задач: " + std::to_wstring(done) + L" из " + std::to_wstring(total);
        break;
    }
    case SessStatus::Waiting: out += L"ждёт твоего ответа в терминале"; break;
    case SessStatus::Done:
        out += L"готово";
        if (total) out += L", выполнено задач: " + std::to_wstring(done) + L" из " + std::to_wstring(total);
        break;
    default: out += L"на связи"; break;
    }
    return out + L".";
}

std::vector<Session*> SessionsByRelevance() {
    std::vector<Session*> v;
    for (auto& s : G.store.All()) v.push_back(s.get());
    auto rank = [](const Session* s) {
        if (!s->pendings.empty()) return 4;
        if (s->status == SessStatus::Waiting) return 3;
        if (s->status == SessStatus::Working) return 2;
        if (s->status == SessStatus::Done) return 1;
        return 0;
    };
    std::stable_sort(v.begin(), v.end(), [&](Session* a, Session* b) {
        if (rank(a) != rank(b)) return rank(a) > rank(b);
        return a->lastActivity > b->lastActivity;
    });
    return v;
}

std::wstring SayChats() {
    std::wstring out;
    int n = 0;
    for (auto& c : G.chats.All()) {
        if (c.seen || n >= 3) continue;
        if (c.kind == ChatKind::Info) continue;
        const std::wstring app = c.codex ? L"Codex" : L"Claude";
        out += (out.empty() ? L"" : L" ") + (c.kind == ChatKind::Waiting
                                               ? L"В чате «" + c.title + L"» " + app + L" ждёт твоего ответа."
                                               : L"В чате «" + c.title + L"» " + app + L" закончил работу.");
        ++n;
    }
    return out;
}

std::wstring SayStatus() {
    auto v = SessionsByRelevance();
    std::wstring chats = SayChats();
    if (v.empty()) return chats.empty() ? L"Сейчас Codex тебя не ждёт." : chats;
    std::wstring out = chats.empty() ? L"" : chats + L" ";
    for (size_t i = 0; i < v.size() && i < 3; ++i) out += (i ? L" " : L"") + SessionSentence(*v[i]);
    if (v.size() > 3) out += L" И ещё " + std::to_wstring(v.size() - 3) + L" в островке.";
    return out;
}

std::wstring SayTasks() {
    for (Session* s : SessionsByRelevance()) {
        if (s->tasks.empty()) continue;
        const int total = (int)s->tasks.size(), done = s->TasksDone();
        std::wstring out = L"В проекте " + s->project + L" задач: " + std::to_wstring(total) + L", готово " +
                           std::to_wstring(done) + L".";
        if (done == total) return out + L" Всё сделано!";
        if (const TaskItem* cur = s->CurrentTask())
            out += L" Сейчас: " + LowerFirst(cur->active.empty() ? cur->subject : cur->active) + L".";
        for (auto& t : s->tasks) {
            if (t.status == 0) { out += L" Дальше: " + LowerFirst(t.subject) + L"."; break; }
        }
        return out;
    }
    return G.store.All().empty() ? L"Сейчас нет активных задач." : L"Сейчас нет списка задач.";
}

// Первый запрос среди всех сессий (самый ранний по id).
std::pair<Session*, Pending*> OldestPending() {
    std::pair<Session*, Pending*> best{nullptr, nullptr};
    for (auto& s : G.store.All())
        for (auto& p : s->pendings)
            if (!best.second || p.id < best.second->id) best = {s.get(), &p};
    return best;
}

void HandleCommand(CommandCtx& ctx) {
    const std::string cmd = ctx.in.value("cmd", "");
    std::wstring say;
    bool ok = true;
    if (cmd == "status") say = SayStatus();
    else if (cmd == "tasks") say = SayTasks();
    else if (cmd == "allow" || cmd == "deny") {
        auto [s, p] = OldestPending();
        if (!p) {
            const ChatItem* c = G.chats.LatestUnseen(ChatKind::Waiting);
            if (c) {
                const bool opened = c->codex ? FocusCodexApp() : FocusClaudeApp();
                say = L"Чат «" + c->title + L"» ждёт ответа. " +
                      (opened ? L"Открыла приложение." : L"Не удалось найти окно приложения.");
            } else {
                say = L"Сейчас никто не ждёт ответа.";
            }
        }
        else if (p->isQuestion) {
            Question& q = p->questions[std::min(p->currentQ, p->questions.size() - 1)];
            if (cmd == "allow" && q.multi && !p->chosen[p->currentQ].empty()) {
                DoSubmitMulti(s->id, p->id);
                say = L"Отправила ответ.";
            } else {
                say = L"Там вопрос с вариантами: " + q.question + L" Скажи, например: «Айко, вариант один».";
                ok = false;
            }
        } else {
            std::wstring what = LowerFirst(p->title), where = ProjectWhere(*s);
            if (!p->detail.empty() && p->detail.size() <= 40 && p->detail.find(L'\n') == std::wstring::npos)
                what += L" «" + p->detail + L"»";
            if (cmd == "allow") { DoAllow(s->id, p->id); say = L"Разрешила: " + what + L", " + where + L"."; }
            else { DoDeny(s->id, p->id); say = L"Отклонила: " + what + L", " + where + L"."; }
        }
    } else if (cmd == "choose") {
        int n = ctx.in.value("n", 0);
        std::pair<Session*, Pending*> found{nullptr, nullptr};
        for (auto& s : G.store.All())
            for (auto& p : s->pendings)
                if (p.isQuestion && (!found.second || p.id < found.second->id)) found = {s.get(), &p};
        if (!found.second) { say = L"Сейчас нет вопроса."; ok = false; }
        else {
            Pending* p = found.second;
            Question& q = p->questions[std::min(p->currentQ, p->questions.size() - 1)];
            if (n < 1 || n > (int)q.labels.size()) {
                say = L"Там только " + std::to_wstring(q.labels.size()) + L" варианта.";
                ok = false;
            } else {
                std::wstring label = q.labels[n - 1];
                bool multi = q.multi;
                DoChoose(found.first->id, p->id, n - 1);
                say = multi ? L"Отметила «" + label + L"». Скажи «Айко, разреши», чтобы отправить."
                            : L"Выбрала «" + label + L"».";
            }
        }
    } else if (cmd == "quit") {
        say = L"Островок закрывается.";
        PostMessageW(G.hwnd, WM_CLOSE, 0, 0);
    } else {
        say = L"Не знаю такой команды.";
        ok = false;
    }
    G.lastActiveAt = GetTickCount64();
    json out = {{"ok", ok}, {"say", sprout::Narrow(say)}};
    ctx.out = out.dump(-1, ' ', false, json::error_handler_t::replace);
}

// ---------- карточка сессии ----------
float DrawPendingBlock(Graphics& g, bool draw, Session& s, Pending& p, float x, float y, float w) {
    const float y0 = y;
    const std::string sid = s.id;
    const uint64_t pid = p.id;
    const float btnH = 28;

    if (!p.isQuestion) {
        if (draw) DrawText1(g, p.title, G.fBold.get(), C_AMBER, RectF(x, y, w, 20));
        y += 22;
        if (!p.detail.empty()) {
            float th = std::min(MeasureWrapped(g, p.detail, G.fMono.get(), w - 16), 4 * 16.0f);
            RectF box(x, y, w, th + 12);
            if (draw) {
                FillRound(g, box, 8, Color(255, 20, 20, 22));
                DrawWrapped(g, p.detail, G.fMono.get(), Color(255, 214, 220, 214), RectF(x + 8, y + 6, w - 16, th));
            }
            y += th + 18;
        }
        float bx = x;
        bx += Button(g, draw, bx, y, btnH, L"Разрешить", C_BTN_OK, [sid, pid] { DoAllow(sid, pid); }) + 8;
        bx += Button(g, draw, bx, y, btnH, L"Отклонить", C_BTN_NO, [sid, pid] { DoDeny(sid, pid); }) + 8;
        Button(g, draw, bx, y, btnH, L"В терминале", C_BTN, [sid, pid] { DoTerminal(sid, pid); });
        y += btnH;
        return y - y0;
    }

    const Question& q = p.questions[std::min(p.currentQ, p.questions.size() - 1)];
    std::wstring head = p.title;
    if (p.questions.size() > 1)
        head += L" · " + std::to_wstring(p.currentQ + 1) + L"/" + std::to_wstring(p.questions.size());
    if (!q.header.empty()) head += L" · " + q.header;
    if (draw) DrawText1(g, head, G.fSmall.get(), C_AMBER, RectF(x, y, w, 18));
    y += 20;
    float qh = std::min(MeasureWrapped(g, q.question, G.fBold.get(), w), 3 * 18.0f);
    if (draw) DrawWrapped(g, q.question, G.fBold.get(), C_TEXT, RectF(x, y, w, qh + 2));
    y += qh + 8;

    const auto& chosen = p.chosen[std::min(p.currentQ, p.chosen.size() - 1)];
    for (size_t i = 0; i < q.labels.size() && i < 6; ++i) {
        float dh = q.descs[i].empty() ? 0 : std::min(MeasureWrapped(g, q.descs[i], G.fSmall.get(), w - 24), 2 * 15.0f);
        float oh = 12 + 18 + (dh > 0 ? dh + 2 : 0);
        RectF r(x, y, w, oh);
        if (draw) {
            bool sel = chosen.count((int)i) > 0;
            Color bg = sel ? Color(255, 40, 92, 64) : C_BTN;
            FillRound(g, r, 10, Hovering(r) ? Lighten(bg, 18) : bg);
            std::wstring label = (q.multi ? (sel ? L"☑ " : L"☐ ") : L"") + q.labels[i];
            DrawText1(g, label, G.fBold.get(), C_TEXT, RectF(x + 12, y + 6, w - 24, 18));
            if (dh > 0) DrawWrapped(g, q.descs[i], G.fSmall.get(), C_DIM, RectF(x + 12, y + 25, w - 24, dh + 1));
            int opt = (int)i;
            G.hits.push_back({r, [sid, pid, opt] { DoChoose(sid, pid, opt); }});
        }
        y += oh + 6;
    }
    float bx = x;
    if (q.multi) bx += Button(g, draw, bx, y, btnH, L"Готово", C_BTN_OK, [sid, pid] { DoSubmitMulti(sid, pid); }) + 8;
    Button(g, draw, bx, y, btnH, L"Ответить в терминале", C_BTN, [sid, pid] { DoTerminal(sid, pid); });
    y += btnH;
    return y - y0;
}

// ---------- список задач Claude Code ----------
void TaskIcon(Graphics& g, int status, float x, float y) {
    if (status == 2) {
        Pen pen(C_GREEN, 1.6f);
        pen.SetStartCap(LineCapRound);
        pen.SetEndCap(LineCapRound);
        g.DrawLine(&pen, x + 0.5f, y + 9, x + 3.5f, y + 12);
        g.DrawLine(&pen, x + 3.5f, y + 12, x + 9, y + 5);
    } else if (status == 1) {
        float a = 0.55f + 0.45f * (float)std::fabs(std::sin(Secs() * 3.0));
        Pen ring(Color((BYTE)(255 * a), C_BLUE.GetR(), C_BLUE.GetG(), C_BLUE.GetB()), 1.6f);
        g.DrawEllipse(&ring, x + 0.5f, y + 4.0f, 9.0f, 9.0f);
        SolidBrush b(C_BLUE);
        g.FillPie(&b, x + 2.5f, y + 6.0f, 5.0f, 5.0f, -90.0f, (float)std::fmod(Secs() * 220.0, 360.0));
    } else {
        Pen ring(Color(255, 95, 95, 105), 1.3f);
        g.DrawEllipse(&ring, x + 1.0f, y + 4.5f, 8.0f, 8.0f);
    }
}

float DrawTasks(Graphics& g, bool draw, Session& s, float x, float y, float w) {
    const float y0 = y;
    const int total = (int)s.tasks.size(), done = s.TasksDone();
    const bool compact = !s.pendings.empty();

    std::wstring label = L"Задачи " + std::to_wstring(done) + L"/" + std::to_wstring(total);
    if (draw) {
        float lw = MeasureWidth(g, label, G.fSmall.get()) + 6;
        DrawText1(g, label, G.fSmall.get(), C_DIM, RectF(x, y, lw, 16));
        RectF bar(x + lw + 4, y + 6, w - lw - 4, 5);
        FillRound(g, bar, 2.5f, Color(255, 52, 52, 58));
        if (done > 0) FillRound(g, RectF(bar.X, bar.Y, bar.Width * done / total, bar.Height), 2.5f, C_GREEN);
    }
    y += 18;

    // какие строки показать: окно вокруг текущей задачи
    int firstOpen = total;
    for (int i = 0; i < total; ++i) if (s.tasks[i].status != 2) { firstOpen = i; break; }
    int maxRows = compact ? 1 : 5;
    int start = compact ? std::min(firstOpen, total - 1) : std::max(0, std::min(firstOpen - 1, total - maxRows));
    int end = std::min(total, start + maxRows);
    for (int i = start; i < end; ++i) {
        const TaskItem& t = s.tasks[i];
        if (draw) {
            TaskIcon(g, t.status, x, y);
            std::wstring text = (t.status == 1 && !t.active.empty()) ? t.active : t.subject;
            Color c = t.status == 2 ? C_DIM : (t.status == 1 ? C_TEXT : Color(255, 175, 175, 185));
            DrawText1(g, text, t.status == 1 ? G.fBold.get() : G.fText.get(), c, RectF(x + 16, y, w - 16, 18));
        }
        y += 19;
    }
    int hidden = total - (end - start);
    if (hidden > 0 && !compact) {
        if (draw) DrawText1(g, L"…ещё " + std::to_wstring(hidden), G.fSmall.get(), C_DIM, RectF(x + 16, y, w - 16, 15));
        y += 16;
    }
    return y - y0 + 4;
}

float DrawCard(Graphics& g, bool draw, Session& s, float x, float y, float w, ULONGLONG now) {
    const float y0 = y;
    const float ix = x + CARD_PAD, iw = w - CARD_PAD * 2;
    y += CARD_PAD;

    Color dot = C_DIM;
    std::wstring status;
    switch (s.status) {
    case SessStatus::Working: dot = C_BLUE; status = L"работает · " + Elapsed(now - s.started); break;
    case SessStatus::Waiting: dot = C_AMBER; status = L"ждёт тебя"; break;
    case SessStatus::Done:    dot = C_GREEN; status = L"готово"; break;
    default:                  status = L"на связи"; break;
    }
    if (draw) {
        float pulse = s.status == SessStatus::Working ? 0.6f + 0.4f * (float)std::sin(Secs() * 5) : 1.0f;
        SolidBrush db(Color((BYTE)(255 * pulse), dot.GetR(), dot.GetG(), dot.GetB()));
        g.FillEllipse(&db, ix, y + 6, 8.0f, 8.0f);
        float sw = MeasureWidth(g, status, G.fSmall.get());
        DrawText1(g, s.project, G.fBold.get(), C_TEXT, RectF(ix + 14, y, iw - 14 - sw - 8, 20));
        DrawText1(g, status, G.fSmall.get(), C_DIM, RectF(ix + iw - sw - 2, y, sw + 4, 20), StringAlignmentFar);
    }
    y += 24;

    if (!s.tasks.empty()) y += DrawTasks(g, draw, s, ix, y, iw);

    size_t maxSteps = s.pendings.empty() ? (s.tasks.empty() ? 3 : 2) : (s.tasks.empty() ? 1 : 0);
    size_t start = s.steps.size() > maxSteps ? s.steps.size() - maxSteps : 0;
    for (size_t i = start; i < s.steps.size(); ++i) {
        const Step& st = s.steps[i];
        if (draw) {
            if (st.failed) {
                Pen pen(C_RED, 1.6f);
                g.DrawLine(&pen, ix + 1, y + 5, ix + 8, y + 12);
                g.DrawLine(&pen, ix + 8, y + 5, ix + 1, y + 12);
            } else if (st.done) {
                Pen pen(C_GREEN, 1.6f);
                pen.SetStartCap(LineCapRound);
                pen.SetEndCap(LineCapRound);
                g.DrawLine(&pen, ix + 0.5f, y + 9, ix + 3.5f, y + 12);
                g.DrawLine(&pen, ix + 3.5f, y + 12, ix + 9, y + 5);
            } else {
                float a = 0.45f + 0.55f * (float)std::fabs(std::sin(Secs() * 3.2));
                SolidBrush b(Color((BYTE)(255 * a), C_BLUE.GetR(), C_BLUE.GetG(), C_BLUE.GetB()));
                g.FillEllipse(&b, ix + 1.5f, y + 5.5f, 6.0f, 6.0f);
            }
            DrawText1(g, st.text, G.fText.get(), st.done ? C_DIM : C_TEXT, RectF(ix + 16, y, iw - 16, 18));
        }
        y += 19;
    }
    if (!s.note.empty() && s.pendings.empty()) {
        Color nc = s.status == SessStatus::Waiting ? C_AMBER : (s.status == SessStatus::Done ? C_GREEN : C_DIM);
        if (draw) DrawText1(g, s.note, G.fText.get(), nc, RectF(ix, y, iw, 18));
        y += 20;
    }
    if (!s.pendings.empty()) {
        y += 6;
        y += DrawPendingBlock(g, draw, s, s.pendings.front(), ix, y, iw);
        if (s.pendings.size() > 1) {
            y += 6;
            std::wstring more = L"В очереди ещё " + std::to_wstring(s.pendings.size() - 1);
            if (draw) DrawText1(g, more, G.fSmall.get(), C_DIM, RectF(ix, y, iw, 16));
            y += 16;
        }
    }
    y += CARD_PAD;
    return y - y0;
}

// ---------- чаты приложения Claude ----------
std::wstring Ago(ULONGLONG ms) {
    ULONGLONG m = ms / 60000;
    if (m < 1) return L"только что";
    if (m < 60) return std::to_wstring(m) + L" мин назад";
    return std::to_wstring(m / 60) + L" ч назад";
}

float DrawChats(Graphics& g, bool draw, float L, float y, float W, ULONGLONG now) {
    auto& chats = G.chats.All();
    if (chats.empty()) return 0;
    const float y0 = y;
    if (draw) DrawText1(g, L"Чаты Codex", G.fSmall.get(), C_DIM, RectF(L + 4, y, W, 16));
    y += 18;
    int shown = 0;
    for (auto& c : chats) {
        if (shown >= 4) break;
        const bool waiting = c.kind == ChatKind::Waiting && !c.seen;
        const bool fresh = !c.seen;
        std::wstring right = waiting ? L"ждёт ответа" : Ago(now - c.at);
        float tw = MeasureWidth(g, right, G.fSmall.get());
        float th = c.text.empty() ? 0 : std::min(MeasureWrapped(g, c.text, G.fText.get(), W - 34), 2 * 17.0f);
        float h = 12 + 20 + (th > 0 ? th + 2 : 0) + 10;
        RectF card(L, y, W, h);
        if (draw) {
            Color bg = waiting ? Color(255, 48, 40, 22) : C_CARD;
            FillRound(g, card, 14, Hovering(card) ? Lighten(bg, 8) : bg);
            Color dot = waiting ? C_AMBER : (c.kind == ChatKind::Done ? C_GREEN : C_DIM);
            if (!fresh) dot = Color(255, 90, 90, 98);
            float pulse = waiting ? 0.6f + 0.4f * (float)std::sin(Secs() * 4) : 1.0f;
            SolidBrush db(Color((BYTE)(255 * pulse), dot.GetR(), dot.GetG(), dot.GetB()));
            g.FillEllipse(&db, L + 12, y + 18, 8.0f, 8.0f);
            DrawText1(g, c.title, G.fBold.get(), fresh ? C_TEXT : C_DIM, RectF(L + 26, y + 12, W - 26 - tw - 16, 20));
            DrawText1(g, right, G.fSmall.get(), waiting ? C_AMBER : C_DIM, RectF(L + W - tw - 14, y + 12, tw + 4, 20),
                      StringAlignmentFar);
            if (th > 0) DrawWrapped(g, c.text, G.fText.get(), fresh ? Color(255, 200, 200, 208) : C_DIM,
                                    RectF(L + 26, y + 33, W - 34, th + 1));
            std::wstring key = c.key;
            G.hits.push_back({card, [key] {
                ChatItem* it = G.chats.Find(key);
                if (it) it->seen = true;
                PlaySfx(Sfx::Click);
                if (!(it && it->codex ? FocusCodexApp() : FocusClaudeApp())) PlaySfx(Sfx::Deny);
            }});
        }
        y += h + 8;
        ++shown;
    }
    if (chats.size() > 4) {
        if (draw) DrawText1(g, L"…и ещё " + std::to_wstring(chats.size() - 4), G.fSmall.get(), C_DIM, RectF(L + 4, y, W, 16));
        y += 18;
    }
    return y - y0 + 4;
}

// ---------- панель целиком ----------
float LayoutPanel(Graphics& g, bool draw, const RectF& shape, ULONGLONG now) {
    const float L = shape.X + 14, W = shape.Width - 28;
    float y = 10;

    // шапка
    if (draw) {
        PetAnim a = G.pet;
        DrawPet(g, L + 20, y + 24, 32, a);
        const AikoState& ak = Aiko();
        DrawText1(g, ak.linked ? ak.name : L"Sprout", G.fTitle.get(), C_TEXT, RectF(L + 46, y + 4, 200, 22));
        auto& all = G.store.All();
        int waitingChats = 0;
        for (auto& c : G.chats.All()) waitingChats += (!c.seen && c.kind == ChatKind::Waiting);
        std::wstring sub;
        if (waitingChats == 1) sub = L"1 чат ждёт ответа";
        else if (waitingChats > 1) sub = std::to_wstring(waitingChats) + (waitingChats < 5 ? L" чата ждут ответа" : L" чатов ждут ответа");
        else if (all.empty()) sub = G.chats.All().empty() ? L"всё спокойно" : L"чаты Codex";
        else if (all.size() == 1) sub = L"1 активная задача";
        else if (all.size() < 5) sub = std::to_wstring(all.size()) + L" активные задачи";
        else sub = std::to_wstring(all.size()) + L" активных задач";
        if (ak.linked) sub = AikoPhrase() + L" · " + sub;
        DrawText1(g, sub, G.fSmall.get(), C_DIM, RectF(L + 46, y + 25, 240, 18));
    }
    {
        float bw = 0;
        float right = L + W;
        bw = Button(g, false, 0, 0, 26, L"•••", C_BTN, nullptr, 40);
        Button(g, draw, right - bw, y + 11, 26, L"•••", C_BTN,
               [] { PostMessageW(G.hwnd, WM_SPROUT_TRAY, 0, WM_RBUTTONUP); }, 40);
        float cw = Button(g, false, 0, 0, 26, L"Чат", C_BTN, nullptr);
        Button(g, draw, right - bw - 8 - cw, y + 11, 26, L"Чат", C_BTN, [] { OpenChatWindow(G.inst); });
    }
    y += 54;

    // последняя фраза Айко — «облачко» под шапкой
    const AikoState& ak = Aiko();
    if (ak.linked && !ak.status.empty() && now - ak.statusAt < 15000) {
        float h = std::min(MeasureWrapped(g, ak.status, G.fText.get(), W - 24), 3 * 17.0f);
        if (draw) {
            FillRound(g, RectF(L, y, W, h + 16), 12, Color(255, 26, 44, 34));
            DrawWrapped(g, ak.status, G.fText.get(), Color(255, 170, 230, 190), RectF(L + 12, y + 8, W - 24, h + 1));
        }
        y += h + 24;
    }

    y += DrawChats(g, draw, L, y, W, now);

    auto& all = G.store.All();
    if (all.empty() && !G.chats.All().empty()) {
        // есть только чаты — подсказка не нужна
    } else if (all.empty()) {
        std::wstring msg = ak.linked
            ? L"Пока тихо. Когда Codex закончит задачу или будет ждать ответа, Айко скажет об этом вслух."
            : L"Пока тихо. Ожидаю уведомлений Codex.";
        float h = MeasureWrapped(g, msg, G.fText.get(), W - 24);
        float cardH = h + 24;
        bool needHooks = false;
        if (needHooks) cardH += 38;
        if (draw) {
            FillRound(g, RectF(L, y, W, cardH), 14, C_CARD);
            DrawWrapped(g, msg, G.fText.get(), C_DIM, RectF(L + 12, y + 12, W - 24, h + 2));
        }
        if (needHooks) {
            Button(g, draw, L + 12, y + 12 + h + 8, 28, L"Установить хуки Claude Code", C_BTN_OK,
                   [] { InstallHooks(G.hwnd); G.hooksCheckedAt = 0; });
        }
        y += cardH + 10;
    } else {
        // сначала сессии с запросами, потом свежие
        std::vector<Session*> order;
        for (auto& s : all) order.push_back(s.get());
        std::stable_sort(order.begin(), order.end(), [](Session* a, Session* b) {
            bool pa = !a->pendings.empty(), pb = !b->pendings.empty();
            if (pa != pb) return pa;
            return a->lastActivity > b->lastActivity;
        });
        size_t shown = 0;
        for (Session* s : order) {
            if (y > WIN_H - 120 && shown > 0) break;
            float h = DrawCard(g, false, *s, L, y, W, now);
            if (draw) {
                RectF card(L, y, W, h);
                FillRound(g, card, 14, Hovering(card) && s->pendings.empty() ? Lighten(C_CARD, 6) : C_CARD);
                std::string sid = s->id;
                G.hits.push_back({card, [sid] { FocusSession(sid); }});   // клик по карточке — терминал
                DrawCard(g, true, *s, L, y, W, now);
            }
            y += h + 8;
            ++shown;
        }
        if (shown < order.size()) {
            if (draw) DrawText1(g, L"…и ещё " + std::to_wstring(order.size() - shown), G.fSmall.get(), C_DIM,
                                RectF(L, y, W, 16));
            y += 18;
        }
        if (draw) DrawText1(g, L"Клик по карточке — перейти в терминал", G.fSmall.get(), Color(255, 100, 100, 110),
                            RectF(L, y, W, 16), StringAlignmentCenter);
        y += 20;
    }
    return std::min(y + 4, WIN_H - 4);
}

// Что написать в свёрнутом островке (и какого цвета).
std::wstring PillText(Color& col) {
    Session* s = G.store.MostRelevant();
    const AikoState& ak = Aiko();
    const ULONGLONG now = GetTickCount64();
    const bool aikoTalking = ak.linked && !ak.status.empty() &&
                             (ak.activity == "speaking" || now - ak.statusAt < 6000);
    std::wstring text = ak.linked ? ak.name + L" · " + AikoPhrase() : L"Sprout";
    col = C_DIM;
    const ChatItem* chatWait = G.chats.LatestUnseen(ChatKind::Waiting);
    const ChatItem* chatDone = G.chats.LatestUnseen(ChatKind::Done);
    if (s && !s->pendings.empty()) { text = L"Нужно твоё решение · " + s->project; col = C_AMBER; }
    else if (chatWait) { text = L"Ждёт ответа · " + chatWait->title; col = C_AMBER; }
    else if (aikoTalking) { text = ak.status; col = Color(255, 170, 230, 190); }
    else if (chatDone && now - chatDone->at < 20000) { text = L"Готово · " + chatDone->title; col = C_GREEN; }
    else if (s) {
        if (s->status == SessStatus::Waiting) { text = s->project + L" · ждёт тебя"; col = C_AMBER; }
        else if (s->status == SessStatus::Working) {
            const TaskItem* cur = s->CurrentTask();
            if (cur)
                text = s->project + L" · " + std::to_wstring(s->TasksDone()) + L"/" + std::to_wstring(s->tasks.size()) +
                       L" · " + (cur->active.empty() ? cur->subject : cur->active);
            else
                text = s->project + (s->steps.empty() ? L" · думает…" : L" · " + s->steps.back().text);
            col = C_TEXT;
        } else if (s->status == SessStatus::Done) { text = s->project + L" · готово"; col = C_GREEN; }
        else { text = s->project + (s->note.empty() ? L"" : L" · " + s->note); col = C_DIM; }
    }
    return text;
}

void DrawPill(Graphics& g, const RectF& shape) {
    float h = shape.Height;
    float petSize = std::min(22.0f, h - 14);
    float px = shape.X + 22;
    DrawPet(g, px, h / 2 + 2, petSize, G.pet);   // ниже центра, чтобы листики не упирались в край экрана
    Color col;
    std::wstring text = PillText(col);
    size_t n = G.store.All().size();
    float right = shape.X + shape.Width - 14;
    if (n > 1) {
        std::wstring badge = L"+" + std::to_wstring(n - 1);
        float bw = MeasureWidth(g, badge, G.fSmall.get()) + 10;
        RectF br(right - bw, h / 2 - 9, bw, 18);
        FillRound(g, br, 9, C_BTN);
        DrawText1(g, badge, G.fSmall.get(), C_TEXT, br, StringAlignmentCenter);
        right -= bw + 6;
    }
    DrawText1(g, text, G.fText.get(), col, RectF(px + 18, 0, right - px - 18, h));
}

void BuildIslandPath(GraphicsPath& p, const RectF& r) {
    float x = r.X, w = r.Width, h = r.Height;
    float rad = std::min(h / 2, 18.0f);
    float f = h > 16 ? 8.0f : 0.0f;   // «плечи», как у выреза экрана
    if (f > 0) p.AddArc(x - 2 * f, 0.0f, 2 * f, 2 * f, 270.0f, 90.0f);
    p.AddLine(x, f, x, h - rad);
    p.AddArc(x, h - 2 * rad, 2 * rad, 2 * rad, 180, -90);
    p.AddArc(x + w - 2 * rad, h - 2 * rad, 2 * rad, 2 * rad, 90, -90);
    p.AddLine(x + w, h - rad, x + w, f);
    if (f > 0) p.AddArc(x + w, 0.0f, 2 * f, 2 * f, 180.0f, 90.0f);
    p.CloseFigure();
}

void Render() {
    ULONGLONG now = GetTickCount64();
    const int stride = G.wpx * 4;
    Bitmap bmp(G.wpx, G.hpx, stride, PixelFormat32bppPARGB, (BYTE*)G.bits);
    Graphics g(&bmp);
    g.SetSmoothingMode(SmoothingModeAntiAlias);
    g.SetPixelOffsetMode(PixelOffsetModeHighQuality);
    g.SetTextRenderingHint(TextRenderingHintAntiAliasGridFit);
    g.SetCompositingMode(CompositingModeSourceCopy);
    {
        SolidBrush clear(Color(0, 0, 0, 0));
        g.FillRectangle(&clear, 0, 0, G.wpx, G.hpx);
    }
    g.SetCompositingMode(CompositingModeSourceOver);
    g.ScaleTransform(G.scale, G.scale);

    G.hits.clear();
    if (Cfg().petMode) {
        DrawPet(g, 68, 114, 78, G.pet);
        const AikoState& ak = Aiko();
        if (ak.linked && !ak.status.empty() && ak.activity != "thinking" &&
            now - ak.statusAt < 10000) {
            float h = std::min(126.0f, std::max(62.0f, MeasureWrapped(g, ak.status, G.fText.get(), 184) + 34));
            RectF bubble(116, 10, 232, h);
            DrawCloudBubble(g, bubble);
            DrawWrapped(g, ak.status, G.fText.get(), Color(255, 30, 34, 32),
                        RectF(140, 27, 184, h - 34));
        }
        POINT dst{G.winX, G.winY}, src{0, 0};
        SIZE sz{G.wpx, G.hpx};
        BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
        UpdateLayeredWindow(G.hwnd, nullptr, &dst, &sz, G.memDC, &src, 0, &bf, ULW_ALPHA);
        return;
    }
    RectF shape((WIN_W - G.curW) / 2, 0, G.curW, G.curH);
    GraphicsPath path;
    BuildIslandPath(path, shape);
    {
        SolidBrush bg(C_BG);
        g.FillPath(&bg, &path);
        Pen rim(Color(36, 255, 255, 255), 1.0f);
        g.DrawPath(&rim, &path);
    }

    if (G.curH > 18) {
        Region clip(&path);
        g.SetClip(&clip);
        if (G.content < 0.5f) DrawPill(g, shape);
        else LayoutPanel(g, true, shape, now);
        g.ResetClip();
    }

    POINT dst{G.winX, G.winY}, src{0, 0};
    SIZE sz{G.wpx, G.hpx};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
    UpdateLayeredWindow(G.hwnd, nullptr, &dst, &sz, G.memDC, &src, 0, &bf, ULW_ALPHA);
}

// ---------- кадр анимации ----------
void SetFrameInterval(UINT ms) {
    if (G.timerMs == ms) return;
    G.timerMs = ms;
    SetTimer(G.hwnd, TIMER_FRAME, ms, nullptr);
}

bool IsForegroundFullscreen() {
    HWND fg = GetForegroundWindow();
    if (!fg || fg == G.hwnd || fg == GetDesktopWindow() || fg == GetShellWindow()) return false;
    if (IsZoomed(fg)) return false;
    wchar_t cls[64] = {};
    GetClassNameW(fg, cls, 64);
    if (!lstrcmpW(cls, L"WorkerW") || !lstrcmpW(cls, L"Progman")) return false;
    RECT wr;
    if (!GetWindowRect(fg, &wr)) return false;
    HMONITOR mon = MonitorFromWindow(fg, MONITOR_DEFAULTTONULL);
    HMONITOR mine = MonitorFromWindow(G.hwnd, MONITOR_DEFAULTTOPRIMARY);
    if (!mon || mon != mine) return false;
    MONITORINFO mi{sizeof(mi)};
    GetMonitorInfoW(mon, &mi);
    return wr.left <= mi.rcMonitor.left && wr.top <= mi.rcMonitor.top &&
           wr.right >= mi.rcMonitor.right && wr.bottom >= mi.rcMonitor.bottom;
}

void UpdateWindowVisibility() {
    bool want = !G.userHidden && !G.fsHidden;
    if (want && !G.shown) { ShowWindow(G.hwnd, SW_SHOWNOACTIVATE); G.shown = true; G.needsRedraw = true; }
    if (!want && G.shown) { ShowWindow(G.hwnd, SW_HIDE); G.shown = false; }
}

void Frame() {
    ULONGLONG now = GetTickCount64();
    float dt = G.lastFrame ? std::min(0.1f, (now - G.lastFrame) / 1000.0f) : 0.016f;
    G.lastFrame = now;
    double t = Secs();

    if (now - G.lastCleanup > 30000) { G.store.Cleanup(now); G.chats.Cleanup(now); G.lastCleanup = now; }
    if (now - G.lastFsCheck > 1000) {
        G.lastFsCheck = now;
        G.fsHidden = !Cfg().petMode && IsForegroundFullscreen();
        UpdateWindowVisibility();
    }
    if (Cfg().petMode && now - G.lastTopmostAt > 1000) {
        G.lastTopmostAt = now;
        SetWindowPos(G.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    }
    if (now - G.hooksCheckedAt > 5000) { G.hooksInstalledCache = HooksInstalled(); G.hooksCheckedAt = now; }
    AikoPoll(now);
    const AikoState& ak = Aiko();
    if (!G.shown) { SetFrameInterval(250); return; }

    // курсор в логических координатах окна
    POINT pt;
    GetCursorPos(&pt);
    G.cursor = PointF((pt.x - G.winX) / G.scale, (pt.y - G.winY) / G.scale);
    RectF shape((WIN_W - G.curW) / 2, 0, G.curW, G.curH);
    RectF hoverZone = shape;
    if (G.vis == Vis::Hidden) hoverZone = RectF((WIN_W - PEEK_W - 40) / 2, 0, PEEK_W + 40, 14);
    else hoverZone.Inflate(6, 6);
    bool hov = hoverZone.Contains(G.cursor) && pt.y >= G.winY;
    if (Cfg().petMode) hov = false;
    if (hov && !G.hovered) { G.hoverSince = now; G.leaveSince = 0; }
    if (!hov && G.hovered) G.leaveSince = now;
    G.hovered = hov;

    // какое состояние нужно
    bool anyPending = G.store.AnyPending();
    bool anyWaiting = false;
    for (auto& s : G.store.All()) if (s->status == SessStatus::Waiting) anyWaiting = true;
    bool recentDone = now - G.store.LastDoneAt() < 8000;
    const bool chatWaiting = G.chats.AnyUnseenWaiting();
    const ChatItem* chatDone = G.chats.LatestUnseen(ChatKind::Done);
    if (chatWaiting) anyWaiting = true;
    if (chatDone && now - chatDone->at < 20000) recentDone = true;
    bool aikoActive = ak.linked && (ak.activity == "speaking" || ak.activity == "thinking" ||
                                    (!ak.status.empty() && now - ak.statusAt < 6000));
    bool active = anyPending || anyWaiting || G.store.AnyWorking() || recentDone || aikoActive ||
                  now - G.lastActiveAt < 4000;
    Vis base = active ? Vis::Pill : Vis::Hidden;
    Vis target = base;
    if (G.hovered) {
        target = (G.vis == Vis::Hidden) ? Vis::Pill : (now - G.hoverSince > 160 ? Vis::Panel : G.vis);
    } else if (G.leaveSince && now - G.leaveSince < 450) {
        target = G.vis;
    }
    if (anyPending && Cfg().autoExpand) target = Vis::Panel;
    if (Cfg().petMode) target = Vis::Pill;
    if (G.pressed >= 0) target = G.vis;
    G.vis = target;

    float tw = PEEK_W, th = PEEK_H;
    if (G.vis == Vis::Pill) {
        // ширина по длине текста, чтобы фраза Айко или название чата читались целиком
        Color unused;
        std::wstring pill = PillText(unused);
        float extra = G.store.All().size() > 1 ? 40.0f : 0.0f;
        tw = std::min(430.0f, std::max(PILL_W, 70.0f + extra + 6.6f * (float)pill.size()));
        th = PILL_H;
    }
    if (G.vis == Vis::Panel) {
        tw = PANEL_W;
        // замер высоты панели (без рисования)
        Bitmap tmp(1, 1, PixelFormat32bppPARGB);
        Graphics mg(&tmp);
        mg.SetTextRenderingHint(TextRenderingHintAntiAliasGridFit);
        std::vector<Hit> saved;
        saved.swap(G.hits);
        G.panelH = LayoutPanel(mg, false, RectF((WIN_W - PANEL_W) / 2, 0, PANEL_W, WIN_H), now);
        G.hits.swap(saved);
        th = G.panelH;
    }
    float k = 1.0f - std::exp(-dt * 15.0f);
    G.curW += (tw - G.curW) * k;
    G.curH += (th - G.curH) * k;
    float contentTarget = (G.vis == Vis::Panel && G.curH > PILL_H + 30) ? 1.0f : 0.0f;
    G.content += (contentTarget - G.content) * std::min(1.0f, k * 1.4f);
    bool settled = std::fabs(tw - G.curW) < 0.3f && std::fabs(th - G.curH) < 0.3f;
    if (settled) { G.curW = tw; G.curH = th; }

    // питомец
    bool voiceHigh = ak.linked && ak.activity == "idle" && !ak.sleeping && !ak.paused && ak.volume > 0.22f;
    if (voiceHigh) {
        if (!G.voiceHighAt) G.voiceHighAt = now;
        G.voiceQuietAt = 0;
        if (G.voiceArmed && now - G.voiceHighAt > 120) {
            G.surprisedUntil = now + 900;
            G.voiceArmed = false;
        }
    } else {
        G.voiceHighAt = 0;
        if (!ak.linked || ak.volume < 0.07f) {
            if (!G.voiceQuietAt) G.voiceQuietAt = now;
            if (now - G.voiceQuietAt > 350) G.voiceArmed = true;
        } else G.voiceQuietAt = 0;
    }
    if (ak.linked && ak.mood == "heard" && ak.moodAge < 0.2) G.surprisedUntil = now + 900;
    if (ak.linked && ak.activity == "thinking") G.thinkingUntil = now + 600;
    Mood mood = Mood::Idle;
    const bool fresh = ak.moodAge < 2.6;
    if (ak.linked && (ak.activity == "thinking" ||
                      (ak.activity == "speaking" && now < G.thinkingUntil))) mood = Mood::Working;
    else if (ak.linked && ak.activity == "speaking") mood = Mood::Speaking;
    else if (ak.linked && now < G.surprisedUntil) mood = Mood::Surprised;
    else if (anyPending || anyWaiting) mood = Mood::Waiting;
    else if (t < G.happyUntil) mood = Mood::Happy;
    else if (ak.linked && fresh && ak.mood == "happy") mood = Mood::Happy;
    else if (ak.linked && fresh && ak.mood == "sad") mood = Mood::Sad;
    else if (ak.linked && fresh && ak.mood == "confused") mood = Mood::Confused;
    else if (G.store.AnyWorking()) mood = Mood::Working;
    else if (ak.linked && (ak.sleeping || ak.paused)) mood = Mood::Sleep;
    else if (!ak.linked && G.store.All().empty() && now - G.lastActiveAt > 180000) mood = Mood::Sleep;
    if (ak.linked && ak.mood == "heard" && ak.moodAge < 0.4 && t - G.hopStart > 1.0) G.hopStart = t;   // услышала имя
    float targetVoice = (ak.linked && !ak.sleeping && !ak.paused) ? std::min(1.0f, ak.volume * 1.6f) : 0.0f;
    G.pet.voice += (targetVoice - G.pet.voice) * std::min(1.0f, dt * 12);
    G.pet.mood = mood;
    G.pet.t = t;
    if (t >= G.nextBlink) {
        G.blinkStart = t;
        std::uniform_real_distribution<double> d(2.4, 6.0);
        G.nextBlink = t + d(G.rng);
    }
    double bt = t - G.blinkStart;
    G.pet.blink = (G.blinkStart >= 0 && bt < 0.18) ? (float)(1.0 - std::fabs(bt / 0.09 - 1.0)) : 0.0f;
    double ht = t - G.hopStart;
    if (ht >= 0 && ht < 0.9) {
        double ph = ht / 0.45;
        double frac = ph - std::floor(ph);
        G.pet.hop = (float)(std::sin(frac * 3.14159265) * (ph < 1 ? 1.0 : 0.55));
    } else {
        G.pet.hop = 0;
    }
    // глаза следят за курсором
    float petX = Cfg().petMode ? 68 : (G.content > 0.5f ? (WIN_W - PANEL_W) / 2 + 34 : (WIN_W - G.curW) / 2 + 22);
    float petY = Cfg().petMode ? 114 : (G.content > 0.5f ? 34 : G.curH / 2 + 2);
    float dx = G.cursor.X - petX, dy = G.cursor.Y - petY;
    float dist = std::sqrt(dx * dx + dy * dy) + 1;
    float reach = std::min(1.0f, dist / 240.0f);
    G.pet.lookX += ((dx / dist) * reach - G.pet.lookX) * std::min(1.0f, dt * 10);
    G.pet.lookY += ((dy / dist) * reach - G.pet.lookY) * std::min(1.0f, dt * 10);

    bool idleHidden = G.vis == Vis::Hidden && settled && !G.hovered;
    if (idleHidden) {
        if (G.needsRedraw) { Render(); G.needsRedraw = false; }
        SetFrameInterval(100);   // только следим за курсором
    } else {
        Render();
        G.needsRedraw = true;    // когда спрячемся — перерисуем один раз
        SetFrameInterval(16);
    }
}

// ---------- окно, трей, DPI ----------
float QueryScale() {
    using GetDpiForWindowFn = UINT(WINAPI*)(HWND);
    static auto fn = (GetDpiForWindowFn)GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow");
    UINT dpi = 96;
    if (fn && G.hwnd) dpi = fn(G.hwnd);
    else {
        HDC dc = GetDC(nullptr);
        dpi = GetDeviceCaps(dc, LOGPIXELSX);
        ReleaseDC(nullptr, dc);
    }
    return dpi / 96.0f;
}

void Reposition() {
    G.scale = QueryScale();
    HMONITOR mon = MonitorFromPoint(POINT{0, 0}, MONITOR_DEFAULTTOPRIMARY);
    MONITORINFO mi{sizeof(mi)};
    GetMonitorInfoW(mon, &mi);
    G.wpx = (int)std::ceil((Cfg().petMode ? PET_W : WIN_W) * G.scale);
    G.hpx = (int)std::ceil((Cfg().petMode ? PET_H : WIN_H) * G.scale);
    G.winX = Cfg().petMode
        ? (Cfg().petX >= 0 ? std::clamp(Cfg().petX, (int)mi.rcWork.left, (int)mi.rcWork.right - G.wpx)
                            : mi.rcWork.right - G.wpx - 28)
        : mi.rcMonitor.left + ((mi.rcMonitor.right - mi.rcMonitor.left) - G.wpx) / 2;
    G.winY = Cfg().petMode
        ? (Cfg().petY >= 0 ? std::clamp(Cfg().petY, (int)mi.rcWork.top, (int)mi.rcWork.bottom - G.hpx)
                            : mi.rcWork.bottom - G.hpx - 28)
        : mi.rcMonitor.top;

    if (G.memDC) {
        SelectObject(G.memDC, G.oldBmp);
        DeleteObject(G.dib);
        DeleteDC(G.memDC);
    }
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = G.wpx;
    bi.bmiHeader.biHeight = -G.hpx;   // сверху вниз
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    HDC screen = GetDC(nullptr);
    G.memDC = CreateCompatibleDC(screen);
    G.dib = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &G.bits, nullptr, 0);
    ReleaseDC(nullptr, screen);
    G.oldBmp = (HBITMAP)SelectObject(G.memDC, G.dib);
    SetWindowPos(G.hwnd, HWND_TOPMOST, G.winX, G.winY, G.wpx, G.hpx, SWP_NOACTIVATE);
    G.needsRedraw = true;
}

void AddTrayIcon() {
    G.nid = {};
    G.nid.cbSize = sizeof(G.nid);
    G.nid.hWnd = G.hwnd;
    G.nid.uID = 1;
    G.nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    G.nid.uCallbackMessage = WM_SPROUT_TRAY;
    G.nid.hIcon = G.icon;
    lstrcpynW(G.nid.szTip, L"Айко — островок Codex", ARRAYSIZE(G.nid.szTip));
    Shell_NotifyIconW(NIM_ADD, &G.nid);
}

void Balloon(const std::wstring& title, const std::wstring& text) {
    NOTIFYICONDATAW n = G.nid;
    n.uFlags = NIF_INFO;
    lstrcpynW(n.szInfoTitle, title.c_str(), ARRAYSIZE(n.szInfoTitle));
    lstrcpynW(n.szInfo, text.c_str(), ARRAYSIZE(n.szInfo));
    n.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON;
    Shell_NotifyIconW(NIM_MODIFY, &n);
}

void ShowMenu() {
    static bool open = false;   // повторный вызов изнутри открытого меню закрыл бы его
    if (open) return;
    open = true;
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING | MF_GRAYED, 0, Aiko().linked ? L"Айко на связи" : L"Айко не запущена");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, ID_CHAT, Aiko().linked ? L"Чат с Айко…" : L"Чат…");
    AppendMenuW(m, MF_STRING | (!Aiko().linked ? MF_GRAYED : 0), ID_AIKO_LISTEN,
                Aiko().paused ? L"Включить микрофон Айко" : L"Отключить микрофон Айко");
    AppendMenuW(m, MF_STRING | (Cfg().petMode ? MF_CHECKED : 0), ID_PET_MODE, L"Режим питомца");
    AppendMenuW(m, MF_STRING, ID_DEMO, L"Показать демо");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, ID_OPENCFG, L"Открыть папку настроек");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING | (Cfg().sounds ? MF_CHECKED : 0), ID_SOUNDS, L"Звуки");
    AppendMenuW(m, MF_STRING | (Cfg().autoExpand ? MF_CHECKED : 0), ID_AUTOEXPAND, L"Раскрываться при запросе разрешения");
    AppendMenuW(m, MF_STRING | (IsAutostartEnabled() ? MF_CHECKED : 0), ID_AUTOSTART, L"Запускать вместе с Windows");
    AppendMenuW(m, MF_STRING | (G.userHidden ? MF_CHECKED : 0), ID_HIDE, L"Спрятать островок");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, ID_QUIT, L"Выход");

    POINT pt;
    GetCursorPos(&pt);
    SetForegroundWindow(G.hwnd);
    UINT cmd = TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, G.hwnd, nullptr);
    PostMessageW(G.hwnd, WM_NULL, 0, 0);
    DestroyMenu(m);
    open = false;
    if (cmd) SendMessageW(G.hwnd, WM_COMMAND, cmd, 0);
}

// ---------- демо: фейковая сессия, чтобы посмотреть всё без Claude Code ----------
void InjectDemo(json event) {
    auto* ev = new IncomingEvent();
    event["session_id"] = "demo";
    event["cwd"] = "C:\\projects\\demo-parser";
    ev->envelope = {{"v", 1}, {"pids", json::array()}, {"console", 0}, {"event", event}};
    if (ev->envelope["event"]["hook_event_name"] == "PermissionRequest") {
        static uint64_t demoId = 1ULL << 40;
        auto p = std::make_shared<PendingDecision>();
        p->id = ++demoId;
        p->done = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        ev->pending = p;
    }
    SendMessageW(G.hwnd, WM_SPROUT_EVENT, 0, (LPARAM)ev);
}

void DemoTick() {
    Session* s = G.store.Find("demo");
    bool waiting = s && !s->pendings.empty();
    switch (G.demoStep) {
    case 0:
        InjectDemo({{"hook_event_name", "SessionStart"}});
        InjectDemo({{"hook_event_name", "UserPromptSubmit"}, {"prompt", "Добавь тесты для парсера"}});
        break;
    case 1:
        InjectDemo({{"hook_event_name", "PreToolUse"}, {"tool_name", "Read"}, {"tool_use_id", "d1"},
                    {"tool_input", {{"file_path", "C:\\projects\\demo-parser\\src\\parser.cpp"}}}});
        break;
    case 2:
        InjectDemo({{"hook_event_name", "PostToolUse"}, {"tool_use_id", "d1"}});
        InjectDemo({{"hook_event_name", "PreToolUse"}, {"tool_name", "Edit"}, {"tool_use_id", "d2"},
                    {"tool_input", {{"file_path", "C:\\projects\\demo-parser\\tests\\parser_test.cpp"}}}});
        break;
    case 3:
        InjectDemo({{"hook_event_name", "PostToolUse"}, {"tool_use_id", "d2"}});
        InjectDemo({{"hook_event_name", "PreToolUse"}, {"tool_name", "Bash"}, {"tool_use_id", "d3"},
                    {"tool_input", {{"command", "cmake --build build && ctest --output-on-failure"}}}});
        InjectDemo({{"hook_event_name", "PermissionRequest"}, {"tool_name", "Bash"},
                    {"tool_input", {{"command", "cmake --build build && ctest --output-on-failure"}}}});
        break;
    case 4:
        if (waiting) return;   // ждём, пока пользователь нажмёт кнопку
        InjectDemo({{"hook_event_name", "PostToolUse"}, {"tool_use_id", "d3"}});
        InjectDemo({{"hook_event_name", "PermissionRequest"}, {"tool_name", "AskUserQuestion"},
                    {"tool_input", {{"questions", json::array({
                        {{"question", "Какой фреймворк взять для тестов?"}, {"header", "Тесты"}, {"multiSelect", false},
                         {"options", json::array({
                             {{"label", "GoogleTest"}, {"description", "Привычный, много примеров"}},
                             {{"label", "Catch2"}, {"description", "Один заголовок, лаконичный синтаксис"}}})}}})}}}});
        break;
    case 5:
        if (waiting) return;
        InjectDemo({{"hook_event_name", "Stop"}});
        break;
    default:
        KillTimer(G.hwnd, TIMER_DEMO);
        G.demoStep = -1;
        return;
    }
    ++G.demoStep;
}

LRESULT CALLBACK IslandProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == G.taskbarCreated && G.taskbarCreated) { AddTrayIcon(); return 0; }
    switch (m) {
    case WM_TIMER:
        if (w == TIMER_FRAME) Frame();
        else if (w == TIMER_DEMO) DemoTick();
        return 0;

    case WM_SPROUT_EVENT: {
        std::unique_ptr<IncomingEvent> ev((IncomingEvent*)l);
        ULONGLONG now = GetTickCount64();
        Effects fx;
        G.store.Apply(*ev, now, fx);
        G.lastActiveAt = now;
        if (fx.ask && now - G.lastAskSound > 1500) {
            PlaySfx(Sfx::Ask);
            G.lastAskSound = now;
            if (G.fsHidden || G.userHidden) Balloon(L"Sprout", L"Нужно твоё решение");
        }
        if (fx.ask && !fx.askKind.empty()) AikoNotify(fx.askKind, fx.project);
        if (fx.done && fx.turnMs >= 15000) AikoNotify("done", fx.project);   // короткие ответы не озвучиваем
        if (fx.done) {
            PlaySfx(Sfx::Done);
            G.hopStart = Secs();
            G.happyUntil = Secs() + 2.2;
        }
        SetFrameInterval(16);
        return 0;
    }
    case WM_SPROUT_TOASTS: {
        std::unique_ptr<std::vector<RawToast>> rows((std::vector<RawToast>*)l);
        ULONGLONG now = GetTickCount64();
        ChatEffects fx;
        G.chats.Update(*rows, now, fx);
        const bool aiko = Aiko().linked;
        for (auto& key : fx.waiting) {
            if (const ChatItem* c = G.chats.Find(key))
                AikoNotify(c->codex ? "codex_chat_wait" : "chat_wait", c->title);
            if (!aiko && now - G.lastAskSound > 1500) { PlaySfx(Sfx::Ask); G.lastAskSound = now; }
        }
        for (auto& key : fx.done) {
            if (const ChatItem* c = G.chats.Find(key))
                AikoNotify(c->codex ? "codex_chat_done" : "chat_done", c->title);
            if (!aiko) PlaySfx(Sfx::Done);
            G.hopStart = Secs();
            G.happyUntil = Secs() + 2.2;
        }
        if (!fx.waiting.empty() || !fx.done.empty()) G.lastActiveAt = now;
        G.needsRedraw = true;
        SetFrameInterval(16);
        return 0;
    }

    case WM_SPROUT_COMMAND:
        HandleCommand(*(CommandCtx*)l);
        return 0;

    case WM_SPROUT_PENDING_GONE:
        G.store.PendingGone((uint64_t)l);
        return 0;

    case WM_SPROUT_TRAY:
        if (LOWORD(l) == WM_LBUTTONDBLCLK) OpenChatWindow(G.inst);
        else if (LOWORD(l) == WM_RBUTTONUP || LOWORD(l) == WM_LBUTTONUP || LOWORD(l) == WM_CONTEXTMENU) ShowMenu();
        return 0;

    case WM_COMMAND:
        switch (LOWORD(w)) {
        case ID_CHAT: OpenChatWindow(G.inst); break;
        case ID_AIKO_LISTEN: AikoControl(Aiko().paused); break;
        case ID_PET_MODE:
            Cfg().petMode = !Cfg().petMode;
            SaveConfig();
            Reposition();
            break;
        case ID_INSTALL: InstallHooks(nullptr); G.hooksCheckedAt = 0; break;
        case ID_UNINSTALL: UninstallHooks(nullptr); G.hooksCheckedAt = 0; break;
        case ID_APIKEY: {
            std::wstring key;
            if (PromptText(nullptr, L"API-ключ Anthropic",
                           L"Ключ для чата с Claude (console.anthropic.com). Хранится в Диспетчере учётных данных Windows. "
                           L"Пустое поле — удалить ключ.",
                           key, true))
                SaveApiKey(key);
            break;
        }
        case ID_OPENCFG: ShellExecuteW(nullptr, L"open", AppDataDir().c_str(), nullptr, nullptr, SW_SHOWNORMAL); break;
        case ID_SOUNDS: Cfg().sounds = !Cfg().sounds; SaveConfig(); PlaySfx(Sfx::Click); break;
        case ID_AUTOEXPAND: Cfg().autoExpand = !Cfg().autoExpand; SaveConfig(); break;
        case ID_AUTOSTART: SetAutostart(!IsAutostartEnabled()); break;
        case ID_HIDE: G.userHidden = !G.userHidden; UpdateWindowVisibility(); break;
        case ID_DEMO:
            G.store.Remove("demo");
            G.demoStep = 0;
            DemoTick();
            SetTimer(G.hwnd, TIMER_DEMO, 1100, nullptr);
            break;
        case ID_QUIT: DestroyWindow(h); break;
        }
        return 0;

    case WM_MOUSEACTIVATE:
        return MA_NOACTIVATE;

    case WM_LBUTTONDOWN: {
        if (Cfg().petMode) {
            GetCursorPos(&G.dragAt);
            G.dragOrigin = POINT{G.winX, G.winY};
            G.dragged = false;
            SetCapture(h);
            return 0;
        }
        PointF p(GET_X_LPARAM(l) / G.scale, GET_Y_LPARAM(l) / G.scale);
        G.pressed = -1;
        for (int i = (int)G.hits.size() - 1; i >= 0; --i)
            if (G.hits[i].r.Contains(p)) { G.pressed = i; break; }
        SetCapture(h);
        return 0;
    }
    case WM_LBUTTONUP: {
        if (Cfg().petMode) {
            ReleaseCapture();
            if (G.dragged) {
                Cfg().petX = G.winX;
                Cfg().petY = G.winY;
            } else { G.hopStart = Secs(); G.happyUntil = Secs() + 1.5; }
            SaveConfig();
            return 0;
        }
        ReleaseCapture();
        PointF p(GET_X_LPARAM(l) / G.scale, GET_Y_LPARAM(l) / G.scale);
        int idx = -1;
        for (int i = (int)G.hits.size() - 1; i >= 0; --i)
            if (G.hits[i].r.Contains(p)) { idx = i; break; }
        int pressed = G.pressed;
        G.pressed = -1;
        if (idx >= 0 && idx == pressed && G.hits[idx].fn) {
            auto fn = G.hits[idx].fn;   // копия: fn может перестроить hits
            fn();
        }
        return 0;
    }
    case WM_MOUSEMOVE:
        if (Cfg().petMode && GetCapture() == h) {
            POINT pt;
            GetCursorPos(&pt);
            int dx = pt.x - G.dragAt.x, dy = pt.y - G.dragAt.y;
            if (std::abs(dx) > 3 || std::abs(dy) > 3) G.dragged = true;
            if (G.dragged) {
                G.winX = G.dragOrigin.x + dx;
                G.winY = G.dragOrigin.y + dy;
                SetWindowPos(h, HWND_TOPMOST, G.winX, G.winY, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE);
            }
        }
        return 0;
    case WM_RBUTTONDOWN:
        return 0;
    case WM_RBUTTONUP:
    case WM_CONTEXTMENU:
        // меню — после того, как мышь отпущена и сообщение обработано
        PostMessageW(h, WM_SPROUT_TRAY, 0, WM_RBUTTONUP);
        return 0;

    case WM_DISPLAYCHANGE:
    case WM_DPICHANGED:
    case WM_SETTINGCHANGE:
        Reposition();
        return 0;

    case WM_DESTROY:
        AikoControl(false);
        Shell_NotifyIconW(NIM_DELETE, &G.nid);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

} // namespace

HWND CreateIsland(HINSTANCE inst) {
    G.inst = inst;
    G.t0 = GetTickCount64();
    G.rng.seed((unsigned)G.t0);

    G.ffUi = std::make_unique<FontFamily>(L"Segoe UI");
    if (G.ffUi->GetLastStatus() != Ok) G.ffUi.reset(FontFamily::GenericSansSerif()->Clone());
    G.ffMono = std::make_unique<FontFamily>(L"Consolas");
    if (G.ffMono->GetLastStatus() != Ok) G.ffMono.reset(FontFamily::GenericMonospace()->Clone());
    G.fTitle = std::make_unique<Font>(G.ffUi.get(), 15.0f, FontStyleBold, UnitPixel);
    G.fBold = std::make_unique<Font>(G.ffUi.get(), 13.0f, FontStyleBold, UnitPixel);
    G.fText = std::make_unique<Font>(G.ffUi.get(), 12.5f, FontStyleRegular, UnitPixel);
    G.fSmall = std::make_unique<Font>(G.ffUi.get(), 11.5f, FontStyleRegular, UnitPixel);
    G.fMono = std::make_unique<Font>(G.ffMono.get(), 11.5f, FontStyleRegular, UnitPixel);

    WNDCLASSEXW wc{sizeof(wc)};
    wc.lpfnWndProc = IslandProc;
    wc.hInstance = inst;
    wc.lpszClassName = L"SproutIsland";
    wc.hCursor = LoadCursor(nullptr, IDC_HAND);
    G.icon = MakePetIcon(GetSystemMetrics(SM_CXSMICON) * 2 > 32 ? 64 : 32);
    wc.hIcon = G.icon;
    wc.hIconSm = G.icon;
    RegisterClassExW(&wc);

    G.hwnd = CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                             L"SproutIsland", L"Sprout", WS_POPUP, 0, 0, 10, 10, nullptr, nullptr, inst, nullptr);
    if (!G.hwnd) return nullptr;
    Reposition();
    G.taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
    AddTrayIcon();
    StartToastWatcher(G.hwnd);
    G.lastActiveAt = GetTickCount64();   // при старте ненадолго показываемся
    G.hooksInstalledCache = HooksInstalled();
    G.hooksCheckedAt = GetTickCount64();
    UpdateWindowVisibility();
    SetFrameInterval(16);
    return G.hwnd;
}
