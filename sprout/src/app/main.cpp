// Sprout — островок для Claude Code на Windows.
#include "app.h"
#include "gdiplus_inc.h"
#include <objbase.h>

namespace {
void EnableDpiAwareness() {
    using SetCtxFn = BOOL(WINAPI*)(HANDLE);
    auto fn = (SetCtxFn)GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetProcessDpiAwarenessContext");
    if (fn && fn((HANDLE)-4 /* PER_MONITOR_AWARE_V2 */)) return;
    SetProcessDPIAware();
}
} // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
    HANDLE mutex = CreateMutexW(nullptr, TRUE, L"Local\\SproutClaudeIsland");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(nullptr, L"Sprout уже запущен — ищи значок в трее.", L"Sprout", MB_ICONINFORMATION);
        return 0;
    }

    EnableDpiAwareness();
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    Gdiplus::GdiplusStartupInput gsi;
    ULONG_PTR gdiToken = 0;
    Gdiplus::GdiplusStartup(&gdiToken, &gsi, nullptr);

    LoadConfig();
    InitSounds();

    HWND island = CreateIsland(inst);
    if (!island) {
        MessageBoxW(nullptr, L"Не удалось создать окно Sprout.", L"Sprout", MB_ICONERROR);
        return 1;
    }
    StartPipeServer(island);
    PlaySfx(Sfx::Hello);

    if (!Cfg().firstRunDone) { Cfg().firstRunDone = true; SaveConfig(); }

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    Gdiplus::GdiplusShutdown(gdiToken);
    CoUninitialize();
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    return 0;
}
