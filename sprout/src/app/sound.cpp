// Звуки синтезируются при запуске: никаких файлов, никаких чужих сэмплов.
#include "app.h"
#include <mmsystem.h>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace {
struct Note { double freq, start, dur, vol; };

std::vector<uint8_t> g_wav[5];
const double kPi = 3.14159265358979323846;

std::vector<uint8_t> Render(const std::vector<Note>& notes, double total) {
    const int rate = 44100;
    const int n = (int)(total * rate);
    std::vector<float> mix(n, 0.0f);
    for (const Note& nt : notes) {
        const int s0 = (int)(nt.start * rate);
        const int len = (int)(nt.dur * rate);
        for (int i = 0; i < len && s0 + i < n; ++i) {
            const double t = (double)i / rate;
            const double attack = std::min(1.0, t / 0.006);
            const double env = attack * std::exp(-t * (5.0 / nt.dur));
            // мягкий «колокольчик»: основной тон + октава + чуть-чуть квинты
            const double v = std::sin(2 * kPi * nt.freq * t)
                           + 0.28 * std::sin(2 * kPi * nt.freq * 2 * t)
                           + 0.08 * std::sin(2 * kPi * nt.freq * 3 * t);
            mix[s0 + i] += (float)(v * env * nt.vol);
        }
    }
    std::vector<uint8_t> wav(44 + n * 2);
    auto put32 = [&](size_t off, uint32_t v) { std::memcpy(&wav[off], &v, 4); };
    auto put16 = [&](size_t off, uint16_t v) { std::memcpy(&wav[off], &v, 2); };
    std::memcpy(&wav[0], "RIFF", 4); put32(4, 36 + n * 2);
    std::memcpy(&wav[8], "WAVEfmt ", 8); put32(16, 16); put16(20, 1); put16(22, 1);
    put32(24, rate); put32(28, rate * 2); put16(32, 2); put16(34, 16);
    std::memcpy(&wav[36], "data", 4); put32(40, n * 2);
    for (int i = 0; i < n; ++i) {
        float s = std::max(-1.0f, std::min(1.0f, mix[i] * 0.55f));
        int16_t v = (int16_t)(s * 32000);
        std::memcpy(&wav[44 + i * 2], &v, 2);
    }
    return wav;
}
} // namespace

void InitSounds() {
    // Ноты: C6=1046.5 D6=1174.7 E6=1318.5 G6=1568 A5=880 C7=2093
    g_wav[(int)Sfx::Hello] = Render({{784, 0.00, 0.18, 0.5}, {1046.5, 0.09, 0.30, 0.55}}, 0.45);
    g_wav[(int)Sfx::Ask]   = Render({{1318.5, 0.00, 0.22, 0.5}, {1046.5, 0.12, 0.30, 0.45}}, 0.48);
    g_wav[(int)Sfx::Done]  = Render({{1046.5, 0.00, 0.20, 0.45}, {1318.5, 0.08, 0.20, 0.45},
                                     {1568.0, 0.16, 0.40, 0.50}}, 0.62);
    g_wav[(int)Sfx::Click] = Render({{1760, 0.0, 0.05, 0.25}}, 0.07);
    g_wav[(int)Sfx::Deny]  = Render({{659.3, 0.0, 0.16, 0.45}, {523.3, 0.08, 0.24, 0.40}}, 0.36);
}

void PlaySfx(Sfx s) {
    if (!Cfg().sounds) return;
    const auto& w = g_wav[(int)s];
    if (w.empty()) return;
    PlaySoundW((LPCWSTR)w.data(), nullptr, SND_MEMORY | SND_ASYNC | SND_NODEFAULT);
}
