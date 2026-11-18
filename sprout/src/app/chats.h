// Чаты Claude и Codex: островок узнаёт о них из уведомлений Windows.
#pragma once
#include "app.h"

#define WM_SPROUT_TOASTS (WM_APP + 6)   // lParam = std::vector<RawToast>*

struct RawToast {
    long long id = 0;
    unsigned long long arrival = 0;     // FILETIME (100 нс с 1601 года)
    std::wstring title, body, header;
    bool codex = false;
};

enum class ChatKind { Info, Waiting, Done };

struct ChatItem {
    std::wstring key;                   // название чата
    std::wstring title, text;
    bool codex = false;
    ChatKind kind = ChatKind::Info;
    unsigned long long arrival = 0;
    ULONGLONG at = 0;                   // GetTickCount64, когда пришло
    bool seen = false;                  // пользователь уже видел / ответил
    std::vector<long long> ids;         // уведомления в базе Windows, относящиеся к чату
};

struct ChatEffects {
    std::vector<std::wstring> waiting, done;   // названия чатов с новыми событиями (для звука и голоса Айко)
};

class ChatStore {
public:
    void Update(const std::vector<RawToast>& rows, ULONGLONG now, ChatEffects& fx);
    void Cleanup(ULONGLONG now);
    std::vector<ChatItem>& All() { return items_; }
    ChatItem* Find(const std::wstring& key);
    bool AnyUnseenWaiting() const;
    const ChatItem* LatestUnseen(ChatKind k) const;
private:
    std::vector<ChatItem> items_;
    std::vector<long long> known_;
    bool first_ = true;
};

void StartToastWatcher(HWND target);    // фоновый поток: опрашивает базу уведомлений
bool FocusClaudeApp();                  // вывести окно приложения Claude на передний план (или открыть его)
bool FocusCodexApp();
void ForceForeground(HWND w);
