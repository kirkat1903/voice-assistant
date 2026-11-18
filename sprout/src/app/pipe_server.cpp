// Сервер именованного канала: принимает события от sprout-hook.exe.
#include "app.h"
#include <thread>

namespace {

std::atomic<uint64_t> g_nextId{1};

void HandleClient(HANDLE h, HWND target) {
    std::string raw;
    if (!sprout::ReadPipeMessage(h, raw)) { CloseHandle(h); return; }

    json env = json::parse(raw, nullptr, false);
    if (!env.is_discarded() && env.is_object() && env.contains("cmd")) {
        // Команда от Айко («что делает Claude», «разреши»): ответ готовит UI-поток.
        CommandCtx ctx;
        ctx.in = env;
        DWORD_PTR res = 0;
        if (!SendMessageTimeoutW(target, WM_SPROUT_COMMAND, 0, (LPARAM)&ctx, SMTO_ABORTIFHUNG, 3000, &res) ||
            ctx.out.empty())
            ctx.out = R"({"ok":false,"say":"Островок не ответил."})";
        sprout::WriteAll(h, ctx.out);
        FlushFileBuffers(h);
        DisconnectNamedPipe(h);
        CloseHandle(h);
        return;
    }
    if (env.is_discarded() || !env.is_object() || !env.contains("event")) {
        CloseHandle(h);
        return;
    }

    auto* ev = new IncomingEvent();
    ev->envelope = std::move(env);
    const std::string name = ev->envelope["event"].value("hook_event_name", "");

    if (name != "PermissionRequest") {
        if (!PostMessageW(target, WM_SPROUT_EVENT, 0, (LPARAM)ev)) delete ev;
        DisconnectNamedPipe(h);
        CloseHandle(h);
        return;
    }

    auto p = std::make_shared<PendingDecision>();
    p->id = g_nextId++;
    p->done = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    ev->pending = p;
    const uint64_t id = p->id;

    bool clientGone = false;
    if (PostMessageW(target, WM_SPROUT_EVENT, 0, (LPARAM)ev)) {
        const ULONGLONG deadline = GetTickCount64() + (ULONGLONG)std::max(10, Cfg().permissionTimeoutSec) * 1000ULL;
        for (;;) {
            if (WaitForSingleObject(p->done, 250) == WAIT_OBJECT_0) break;
            // Claude Code прервал хук (Esc, Ctrl+C) -> клиент исчез.
            DWORD avail = 0;
            if (!PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr)) { clientGone = true; break; }
            if (GetTickCount64() > deadline) break;
        }
    } else {
        delete ev;
    }

    std::string reply;
    {
        std::lock_guard<std::mutex> lock(p->mu);
        if (!p->finished) { p->finished = true; reply = R"({"behavior":"pass"})"; }
        else reply = p->reply;
    }
    if (!clientGone) {
        sprout::WriteAll(h, reply);
        FlushFileBuffers(h);
    }
    PostMessageW(target, WM_SPROUT_PENDING_GONE, 0, (LPARAM)id);
    DisconnectNamedPipe(h);
    CloseHandle(h);
}

void ServerLoop(HWND target) {
    const std::wstring name = sprout::PipeName();
    for (;;) {
        HANDLE h = CreateNamedPipeW(
            name.c_str(), PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_UNLIMITED_INSTANCES, sprout::kPipeBufferSize, sprout::kPipeBufferSize, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) { Sleep(1000); continue; }

        BOOL connected = ConnectNamedPipe(h, nullptr) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (!connected) { CloseHandle(h); continue; }
        std::thread(HandleClient, h, target).detach();
    }
}

} // namespace

void StartPipeServer(HWND target) {
    std::thread(ServerLoop, target).detach();
}
