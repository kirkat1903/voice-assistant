// Окно чата: через Айко (бесплатно) или, если есть свой ключ, через Anthropic API.
#include "app.h"
#include <thread>

namespace {

struct ChatReply {
    bool ok = false;
    std::string text;
};

struct ChatState {
    HWND wnd = nullptr, log = nullptr, input = nullptr, send = nullptr, reset = nullptr, status = nullptr;
    HFONT font = nullptr, fontSmall = nullptr;
    WNDPROC inputOrig = nullptr;
    json history = json::array();
    bool busy = false;
};
ChatState C;

const int ID_SEND = 1, ID_NEW = 2;

std::wstring ToCrlf(const std::wstring& s) {
    std::wstring out;
    out.reserve(s.size() + 16);
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == L'\n' && (i == 0 || s[i - 1] != L'\r')) out += L'\r';
        out += s[i];
    }
    return out;
}

void AppendLog(const std::wstring& text) {
    int len = GetWindowTextLengthW(C.log);
    SendMessageW(C.log, EM_SETSEL, len, len);
    SendMessageW(C.log, EM_REPLACESEL, FALSE, (LPARAM)ToCrlf(text).c_str());
    SendMessageW(C.log, EM_SCROLLCARET, 0, 0);
}

// Через кого идёт чат: Айко (бесплатно, её собеседник и общая история) или Anthropic API по ключу.
std::wstring Who() { return Aiko().linked ? Aiko().name : L"Sprout"; }

void SetBusy(bool busy) {
    C.busy = busy;
    EnableWindow(C.send, !busy);
    std::wstring via = Aiko().linked
        ? L"Через " + Aiko().name + L" · бесплатно"
        : (LoadApiKey().empty() ? L"Айко не запущена" : L"Anthropic API · " + sprout::Widen(Cfg().model));
    std::wstring line = busy ? Who() + L" думает…  ·  " + via : via + L"  ·  Enter — отправить";
    SetWindowTextW(C.status, line.c_str());
}

// HTTPS POST https://api.anthropic.com/v1/messages (запасной путь, если есть свой ключ)
ChatReply CallApi(const std::string& key, const std::string& body) {
    ChatReply r;
    std::wstring headers = L"content-type: application/json\r\nanthropic-version: 2023-06-01\r\nx-api-key: " +
                           sprout::Widen(key) + L"\r\n";
    HttpResult h = HttpPost(L"api.anthropic.com", 443, true, L"/v1/messages", headers, body, 180000);
    if (!h.ok) { r.text = "Сетевая ошибка (" + h.error + "). Проверь интернет и прокси."; return r; }
    json j = json::parse(h.body, nullptr, false);
    if (h.status != 200) {
        std::string msg = "HTTP " + std::to_string(h.status);
        if (!j.is_discarded() && j.contains("error") && j["error"].is_object())
            msg += ": " + j["error"].value("message", std::string());
        if (h.status == 401) msg += "\nПроверь API-ключ (меню Sprout → API-ключ Anthropic).";
        r.text = msg;
        return r;
    }
    if (j.is_discarded() || !j.contains("content")) { r.text = "Непонятный ответ API"; return r; }
    for (const auto& block : j["content"])
        if (block.value("type", "") == "text") r.text += block.value("text", std::string());
    r.ok = true;
    return r;
}

void Send() {
    if (C.busy) return;
    int len = GetWindowTextLengthW(C.input);
    if (len == 0) return;
    std::wstring text(len, L'\0');
    GetWindowTextW(C.input, &text[0], len + 1);
    size_t a = text.find_first_not_of(L" \t\r\n");
    if (a == std::wstring::npos) return;

    const bool viaAiko = Aiko().linked;
    std::string key = viaAiko ? std::string() : LoadApiKey();
    if (!viaAiko && key.empty()) {
        AppendLog(L"Айко сейчас не запущена, а чат идёт через неё — бесплатно, на её ИИ (Groq, Gemini или Ollama).\n"
                  L"Запусти Айко (tray_app.py или ярлык «Айко») и напиши ещё раз.\n\n");
        return;
    }

    SetWindowTextW(C.input, L"");
    AppendLog(L"Ты:\n" + text + L"\n\n");
    C.history.push_back({{"role", "user"}, {"content", sprout::Narrow(text)}});
    SetBusy(true);
    HWND target = C.wnd;

    if (viaAiko) {
        std::string msg = sprout::Narrow(text);
        std::thread([msg, target] {
            auto* r = new ChatReply();
            std::string reply;
            if (AikoChat(msg, reply)) {
                r->ok = reply.rfind("[Айко]", 0) != 0 && reply.rfind("Айко не ответила", 0) != 0 &&
                        reply.rfind("Айко вернула", 0) != 0;
                r->text = reply;
            } else {
                r->text = "Айко отключилась. Запусти её и попробуй ещё раз.";
            }
            if (!PostMessageW(target, WM_SPROUT_CHAT_REPLY, 0, (LPARAM)r)) delete r;
        }).detach();
        return;
    }

    json body;
    body["model"] = Cfg().model;
    body["max_tokens"] = Cfg().maxTokens;
    body["system"] = "You are Aiko, chatting from Sprout, a small desktop companion for Windows. Be concise and "
                     "friendly. Plain text only. Reply in the user's language.";
    body["messages"] = C.history;
    std::string payload = body.dump(-1, ' ', false, json::error_handler_t::replace);
    std::thread([key, payload, target] {
        auto* r = new ChatReply(CallApi(key, payload));
        if (!PostMessageW(target, WM_SPROUT_CHAT_REPLY, 0, (LPARAM)r)) delete r;
    }).detach();
}

LRESULT CALLBACK InputProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_KEYDOWN && w == VK_RETURN && !(GetKeyState(VK_SHIFT) & 0x8000)) { Send(); return 0; }
    if (m == WM_CHAR && (w == L'\r' || w == L'\n') && !(GetKeyState(VK_SHIFT) & 0x8000)) return 0;
    if (m == WM_KEYDOWN && w == 'A' && (GetKeyState(VK_CONTROL) & 0x8000)) { SendMessageW(h, EM_SETSEL, 0, -1); return 0; }
    return CallWindowProcW(C.inputOrig, h, m, w, l);
}

void Layout() {
    RECT rc;
    GetClientRect(C.wnd, &rc);
    int W = rc.right, H = rc.bottom, pad = 10, inputH = 70, btnW = 110, statusH = 20;
    MoveWindow(C.log, pad, pad, W - 2 * pad, H - inputH - statusH - 4 * pad, TRUE);
    MoveWindow(C.input, pad, H - inputH - statusH - 2 * pad, W - btnW - 3 * pad, inputH, TRUE);
    MoveWindow(C.send, W - btnW - pad, H - inputH - statusH - 2 * pad, btnW, inputH / 2 - 3, TRUE);
    MoveWindow(C.reset, W - btnW - pad, H - inputH / 2 - statusH - 2 * pad + 3, btnW, inputH / 2 - 3, TRUE);
    MoveWindow(C.status, pad, H - statusH - pad, W - 2 * pad, statusH, TRUE);
}

LRESULT CALLBACK ChatProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE: {
        HINSTANCE inst = ((CREATESTRUCTW*)l)->hInstance;
        C.wnd = h;
        C.font = CreateFontW(-16, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
        C.fontSmall = CreateFontW(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
        C.log = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
                                WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
                                0, 0, 10, 10, h, nullptr, inst, nullptr);
        SendMessageW(C.log, EM_LIMITTEXT, 0, 0);
        C.input = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
                                  WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN,
                                  0, 0, 10, 10, h, nullptr, inst, nullptr);
        C.send = CreateWindowW(L"BUTTON", L"Отправить", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON, 0, 0, 10, 10, h,
                               (HMENU)(INT_PTR)ID_SEND, inst, nullptr);
        C.reset = CreateWindowW(L"BUTTON", L"Новый чат", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, h,
                                (HMENU)(INT_PTR)ID_NEW, inst, nullptr);
        C.status = CreateWindowW(L"STATIC", L"", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, h, nullptr, inst, nullptr);
        for (HWND c : {C.log, C.input, C.send, C.reset}) SendMessageW(c, WM_SETFONT, (WPARAM)C.font, TRUE);
        SendMessageW(C.status, WM_SETFONT, (WPARAM)C.fontSmall, TRUE);
        C.inputOrig = (WNDPROC)SetWindowLongPtrW(C.input, GWLP_WNDPROC, (LONG_PTR)InputProc);
        if (C.history.empty())
            AppendLog(Aiko().linked ? L"Привет! Это " + Aiko().name + L". Пиши — отвечу здесь, а разговор запомню так же, как голосовой.\n\n"
                                    : std::wstring(L"Привет! Чат работает через Айко — запусти её, и можно болтать бесплатно.\n\n"));
        else {
            for (const auto& msg : C.history) {
                std::wstring who = msg.value("role", "") == "user" ? L"Ты:\n" : Who() + L":\n";
                AppendLog(who + sprout::Widen(msg.value("content", std::string())) + L"\n\n");
            }
        }
        SetBusy(false);
        Layout();
        return 0;
    }
    case WM_SIZE:
        Layout();
        return 0;
    case WM_GETMINMAXINFO:
        ((MINMAXINFO*)l)->ptMinTrackSize = POINT{380, 360};
        return 0;
    case WM_COMMAND:
        if (LOWORD(w) == ID_SEND) Send();
        if (LOWORD(w) == ID_NEW && !C.busy) {
            C.history = json::array();
            SetWindowTextW(C.log, L"");
            AppendLog(L"Новый чат.\n\n");
        }
        return 0;
    case WM_SPROUT_CHAT_REPLY: {
        std::unique_ptr<ChatReply> r((ChatReply*)l);
        if (r->ok) {
            C.history.push_back({{"role", "assistant"}, {"content", r->text}});
            AppendLog(Who() + L":\n" + sprout::Widen(r->text) + L"\n\n");
        } else {
            C.history.erase(C.history.end() - 1);   // неотвеченный вопрос не оставляем в истории
            AppendLog(L"[Ошибка] " + sprout::Widen(r->text) + L"\n\n");
        }
        SetBusy(false);
        SetFocus(C.input);
        return 0;
    }
    case WM_SETFOCUS:
        SetFocus(C.input);
        return 0;
    case WM_CLOSE:
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        DeleteObject(C.font);
        DeleteObject(C.fontSmall);
        C.wnd = nullptr;
        C.busy = false;
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

} // namespace

void OpenChatWindow(HINSTANCE inst) {
    if (C.wnd) {
        ShowWindow(C.wnd, SW_SHOWNORMAL);
        SetForegroundWindow(C.wnd);
        return;
    }
    static bool registered = false;
    if (!registered) {
        WNDCLASSEXW wc{sizeof(wc)};
        wc.lpfnWndProc = ChatProc;
        wc.hInstance = inst;
        wc.lpszClassName = L"SproutChat";
        wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
        wc.hIcon = (HICON)SendMessageW(FindWindowW(L"SproutIsland", nullptr), WM_GETICON, ICON_BIG, 0);
        RegisterClassExW(&wc);
        registered = true;
    }
    int W = 520, H = 640;
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    int x = (wa.left + wa.right - W) / 2, y = wa.top + 70;
    HWND h = CreateWindowExW(0, L"SproutChat", Aiko().linked ? (L"Чат с " + Aiko().name).c_str() : L"Sprout — чат", WS_OVERLAPPEDWINDOW, x, y, W, H,
                             nullptr, nullptr, inst, nullptr);
    ShowWindow(h, SW_SHOWNORMAL);
    SetForegroundWindow(h);
}
