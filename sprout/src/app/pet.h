// Росток — оригинальный персонаж Sprout: мятный кругляш с двумя листиками.
#pragma once
#include "gdiplus_inc.h"

enum class Mood { Idle, Working, Waiting, Happy, Sleep, Speaking, Sad, Confused, Surprised };

struct PetAnim {
    double t = 0;          // секунды с запуска
    Mood mood = Mood::Idle;
    float lookX = 0, lookY = 0;   // -1..1, куда смотрят глаза
    float blink = 0;              // 0 — открыты, 1 — закрыты
    float hop = 0;                // 0..1, высота прыжка
    float voice = 0;              // 0..1, громкость микрофона Айко: Росток «прислушивается»
    bool badge = true;            // рисовать «!» / «?» над головой
};

// cx, cy — центр; size — диаметр тела в логических пикселях.
void DrawPet(Gdiplus::Graphics& g, float cx, float cy, float size, const PetAnim& a);

// Иконка для трея (32x32), нарисованная тем же кодом.
HICON MakePetIcon(int px);
