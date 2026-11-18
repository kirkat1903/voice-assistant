// Небольшой HTTP-клиент на WinHTTP (без внешних библиотек).
#include "app.h"
#include <winhttp.h>

HttpResult HttpPost(const std::wstring& host, int port, bool secure, const std::wstring& path,
                    const std::wstring& headers, const std::string& body, int timeoutMs, const wchar_t* verb) {
    HttpResult r;
    // Локальный мост Айко — без прокси (VPN-прокси не знает про 127.0.0.1).
    const bool local = host == L"127.0.0.1" || host == L"localhost";
#ifdef WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY
    DWORD access = local ? WINHTTP_ACCESS_TYPE_NO_PROXY : WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY;
#else
    DWORD access = local ? WINHTTP_ACCESS_TYPE_NO_PROXY : WINHTTP_ACCESS_TYPE_DEFAULT_PROXY;
#endif
    HINTERNET ses = WinHttpOpen(L"Sprout/1.1", access, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses && !local)
        ses = WinHttpOpen(L"Sprout/1.1", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses) { r.error = "WinHttpOpen: " + std::to_string(GetLastError()); return r; }
    WinHttpSetTimeouts(ses, std::min(timeoutMs, 10000), std::min(timeoutMs, 15000), 30000, timeoutMs);
    HINTERNET con = WinHttpConnect(ses, host.c_str(), (INTERNET_PORT)port, 0);
    HINTERNET req = con ? WinHttpOpenRequest(con, verb, path.c_str(), nullptr, WINHTTP_NO_REFERER,
                                             WINHTTP_DEFAULT_ACCEPT_TYPES, secure ? WINHTTP_FLAG_SECURE : 0)
                        : nullptr;
    if (!req) {
        r.error = "connect: " + std::to_string(GetLastError());
        if (con) WinHttpCloseHandle(con);
        WinHttpCloseHandle(ses);
        return r;
    }
    BOOL ok = WinHttpSendRequest(req, headers.empty() ? WINHTTP_NO_ADDITIONAL_HEADERS : headers.c_str(),
                                 headers.empty() ? 0 : (DWORD)-1L, (LPVOID)body.data(), (DWORD)body.size(),
                                 (DWORD)body.size(), 0) &&
              WinHttpReceiveResponse(req, nullptr);
    if (ok) {
        DWORD status = 0, sz = sizeof(status);
        WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX,
                            &status, &sz, WINHTTP_NO_HEADER_INDEX);
        r.status = (int)status;
        for (;;) {
            DWORD avail = 0;
            if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0) break;
            std::string chunk(avail, '\0');
            DWORD read = 0;
            if (!WinHttpReadData(req, &chunk[0], avail, &read)) break;
            r.body.append(chunk.data(), read);
        }
        r.ok = true;
    } else {
        DWORD e = GetLastError();
        r.error = (e == ERROR_WINHTTP_TIMEOUT) ? "timeout" : "network " + std::to_string(e);
    }
    WinHttpCloseHandle(req);
    WinHttpCloseHandle(con);
    WinHttpCloseHandle(ses);
    return r;
}
