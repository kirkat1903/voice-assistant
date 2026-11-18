#include "pet.h"
#include <cmath>

using namespace Gdiplus;

namespace {
const float kPi = 3.14159265f;

void Leaf(Graphics& g, float x, float y, float len, float wid, float angleDeg, const Color& c1, const Color& c2) {
    GraphicsState st = g.Save();
    g.TranslateTransform(x, y);
    g.RotateTransform(angleDeg);
    GraphicsPath path;
    // лист: две дуги Безье от основания к кончику
    path.AddBezier(PointF(0, 0), PointF(wid, -len * 0.25f), PointF(wid * 0.8f, -len * 0.8f), PointF(0, -len));
    path.AddBezier(PointF(0, -len), PointF(-wid * 0.8f, -len * 0.8f), PointF(-wid, -len * 0.25f), PointF(0, 0));
    LinearGradientBrush br(PointF(0, 0), PointF(0, -len), c1, c2);
    g.FillPath(&br, &path);
    Pen vein(Color(90, 20, 80, 45), std::max(0.6f, wid * 0.12f));
    g.DrawLine(&vein, 0.0f, -len * 0.1f, 0.0f, -len * 0.82f);
    g.Restore(st);
}
} // namespace

void DrawPet(Graphics& g, float cx, float cy, float s, const PetAnim& a) {
    const float t = (float)a.t;
    float breathSpeed = a.mood == Mood::Working ? 4.2f : (a.mood == Mood::Sleep ? 1.2f : 2.2f);
    float breath = std::sin(t * breathSpeed) * (a.mood == Mood::Sleep ? 0.045f : 0.03f);
    breath += a.voice * 0.07f * (0.6f + 0.4f * std::sin(t * 22.0f));   // слушает: пульсирует в такт голосу

    float hopY = -a.hop * s * 0.32f;
    float squash = a.hop > 0 ? 0.04f * std::sin(a.hop * kPi) : 0.0f;
    float bw = s * (1.0f + breath * 0.6f + squash);
    float bh = s * (0.9f - breath - squash);
    float bottom = cy + s * 0.45f + hopY;
    float top = bottom - bh;
    float bcx = cx;

    // тень
    {
        float shadowScale = 1.0f - a.hop * 0.4f;
        SolidBrush sh(Color((BYTE)(55 * shadowScale), 0, 0, 0));
        g.FillEllipse(&sh, cx - s * 0.38f * shadowScale, cy + s * 0.42f, s * 0.76f * shadowScale, s * 0.1f);
    }

    // листики (за телом)
    float sway;
    switch (a.mood) {
    case Mood::Working: sway = std::sin(t * 5.0f) * 10.0f; break;
    case Mood::Waiting: sway = std::sin(t * 9.0f) * 4.0f; break;
    case Mood::Happy:   sway = std::sin(t * 12.0f) * 16.0f; break;
    case Mood::Sleep:   sway = 18.0f + std::sin(t * 1.0f) * 3.0f; break;
    case Mood::Sad:     sway = 24.0f + std::sin(t * 1.4f) * 2.0f; break;
    case Mood::Speaking: sway = std::sin(t * 7.0f) * 8.0f; break;
    case Mood::Confused: sway = std::sin(t * 3.0f) * 14.0f; break;
    default:            sway = std::sin(t * 1.6f) * 7.0f; break;
    }
    float stemTop = top + s * 0.06f;
    float leafLen = s * (a.mood == Mood::Waiting ? 0.36f : (a.mood == Mood::Sad ? 0.27f : 0.31f)) * (1.0f + a.voice * 0.12f);
    Pen stem(Color(255, 60, 150, 90), std::max(1.0f, s * 0.045f));
    stem.SetStartCap(LineCapRound);
    stem.SetEndCap(LineCapRound);
    float stemH = s * 0.12f;
    float tipX = bcx + std::sin(sway * kPi / 180.0f) * stemH;
    g.DrawLine(&stem, bcx, stemTop, tipX, stemTop - stemH);
    Leaf(g, tipX, stemTop - stemH, leafLen, s * 0.13f, -48 + sway, Color(255, 64, 168, 98), Color(255, 132, 214, 146));
    Leaf(g, tipX, stemTop - stemH, leafLen * 0.86f, s * 0.12f, 44 + sway, Color(255, 58, 156, 90), Color(255, 118, 204, 132));

    // тело
    RectF body(bcx - bw / 2, top, bw, bh);
    {
        GraphicsPath p;
        p.AddEllipse(body);
        PathGradientBrush pg(&p);
        pg.SetCenterColor(Color(255, 196, 241, 208));
        Color surround[] = {Color(255, 112, 200, 142)};
        int count = 1;
        pg.SetSurroundColors(surround, &count);
        pg.SetCenterPoint(PointF(bcx - bw * 0.15f, top + bh * 0.3f));
        g.FillEllipse(&pg, body);
        Pen rim(Color(70, 30, 110, 60), std::max(0.8f, s * 0.02f));
        g.DrawEllipse(&rim, body);
        // блик
        SolidBrush shine(Color(110, 255, 255, 255));
        g.FillEllipse(&shine, bcx - bw * 0.3f, top + bh * 0.14f, bw * 0.16f, bh * 0.1f);
    }

    // лицо
    float faceCX = bcx + a.lookX * s * 0.05f;
    float faceCY = top + bh * 0.52f + a.lookY * s * 0.035f;
    float eyeDX = s * 0.17f;
    float eyeW = s * 0.12f, eyeH = s * (a.mood == Mood::Surprised ? 0.21f : 0.16f);
    Color ink(255, 34, 46, 40);
    SolidBrush inkB(ink);
    Pen inkPen(ink, std::max(1.0f, s * 0.04f));
    inkPen.SetStartCap(LineCapRound);
    inkPen.SetEndCap(LineCapRound);

    if (a.mood == Mood::Sad && a.blink < 0.85f) {
        for (int side = -1; side <= 1; side += 2) {
            float ex = faceCX + side * eyeDX;
            g.FillEllipse(&inkB, ex - eyeW / 2, faceCY - eyeH * 0.3f, eyeW, eyeH * 0.75f);
            Pen brow(ink, std::max(0.8f, s * 0.025f));
            brow.SetStartCap(LineCapRound);
            brow.SetEndCap(LineCapRound);
            g.DrawLine(&brow, ex - side * eyeW * 0.7f, faceCY - eyeH * 0.55f, ex + side * eyeW * 0.4f, faceCY - eyeH * 0.8f);
        }
    } else if (a.mood == Mood::Happy) {
        for (int side = -1; side <= 1; side += 2) {
            float ex = faceCX + side * eyeDX;
            g.DrawArc(&inkPen, ex - eyeW * 0.6f, faceCY - eyeH * 0.2f, eyeW * 1.2f, eyeH * 0.8f, 200, 140);
        }
    } else if (a.mood == Mood::Sleep || a.blink > 0.85f) {
        for (int side = -1; side <= 1; side += 2) {
            float ex = faceCX + side * eyeDX;
            g.DrawArc(&inkPen, ex - eyeW * 0.55f, faceCY - eyeH * 0.15f, eyeW * 1.1f, eyeH * 0.5f, 20, 140);
        }
    } else {
        float open = 1.0f - a.blink;
        float h = std::max(eyeH * 0.12f, eyeH * open * (a.mood == Mood::Waiting ? 1.12f : 1.0f));
        for (int side = -1; side <= 1; side += 2) {
            float ex = faceCX + side * eyeDX;
            g.FillEllipse(&inkB, ex - eyeW / 2, faceCY - h / 2, eyeW, h);
            if (open > 0.5f) {
                SolidBrush hi(Color(235, 255, 255, 255));
                g.FillEllipse(&hi, ex - eyeW * 0.28f + a.lookX * eyeW * 0.12f, faceCY - h * 0.34f, eyeW * 0.36f, eyeW * 0.36f);
            }
        }
    }

    // щёчки
    if (a.mood == Mood::Happy || a.mood == Mood::Idle || a.mood == Mood::Sleep) {
        SolidBrush cheek(Color(a.mood == Mood::Happy ? 150 : 80, 255, 140, 150));
        for (int side = -1; side <= 1; side += 2) {
            float ex = faceCX + side * (eyeDX + s * 0.09f);
            g.FillEllipse(&cheek, ex - s * 0.06f, faceCY + eyeH * 0.42f, s * 0.12f, s * 0.06f);
        }
    }

    // рот
    float my = faceCY + eyeH * 0.62f;
    Pen mouth(ink, std::max(0.9f, s * 0.03f));
    mouth.SetStartCap(LineCapRound);
    mouth.SetEndCap(LineCapRound);
    if (a.mood == Mood::Speaking) {
        float open = 0.25f + 0.75f * std::fabs(std::sin(t * 13.0f) * std::sin(t * 5.3f + 1.0f));
        float mw = s * 0.11f, mh = s * 0.02f + s * 0.07f * open;
        SolidBrush mb(ink);
        g.FillEllipse(&mb, faceCX - mw / 2, my - s * 0.01f, mw, mh);
    } else if (a.mood == Mood::Sad) {
        g.DrawArc(&mouth, faceCX - s * 0.05f, my + s * 0.01f, s * 0.1f, s * 0.07f, 200, 140);
    } else if (a.mood == Mood::Waiting || a.mood == Mood::Confused || a.mood == Mood::Surprised) {
        float r = a.mood == Mood::Surprised ? s * 0.105f : s * 0.07f;
        g.DrawEllipse(&mouth, faceCX - r / 2, my - s * 0.01f, r, r);
    } else if (a.mood == Mood::Working) {
        g.DrawLine(&mouth, faceCX - s * 0.04f, my + s * 0.02f, faceCX + s * 0.04f, my + s * 0.02f);
        float dropY = my + s * (0.16f + 0.025f * std::sin(t * 4.0f));
        Pen saliva(Color(230, 75, 170, 220), std::max(1.5f, s * 0.025f));
        g.DrawLine(&saliva, faceCX + s * 0.04f, my + s * 0.02f, faceCX + s * 0.07f, dropY);
        SolidBrush drop(Color(245, 90, 195, 235));
        g.FillEllipse(&drop, faceCX + s * 0.025f, dropY - s * 0.01f, s * 0.1f, s * 0.14f);
    } else if (a.mood != Mood::Sleep) {
        float mw = a.mood == Mood::Happy ? s * 0.16f : s * 0.1f;
        g.DrawArc(&mouth, faceCX - mw / 2, my - mw * 0.35f, mw, mw * 0.7f, 20, 140);
    }

    // Знаки состояния над правым краем головы.
    if (a.mood == Mood::Working && a.badge && s >= 28) {
        float bx = bcx + bw * 0.46f, by = top - s * 0.06f;
        float r = std::max(5.0f, s * 0.15f);
        SolidBrush back(Color(235, 24, 55, 44));
        g.FillEllipse(&back, bx - r, by - r, 2 * r, 2 * r);
        Pen track(Color(170, 120, 185, 155), std::max(1.0f, s * 0.025f));
        g.DrawEllipse(&track, bx - r * 0.65f, by - r * 0.65f, r * 1.3f, r * 1.3f);
        Pen arc(Color(255, 170, 250, 205), std::max(1.5f, s * 0.035f));
        arc.SetStartCap(LineCapRound);
        arc.SetEndCap(LineCapRound);
        g.DrawArc(&arc, bx - r * 0.65f, by - r * 0.65f, r * 1.3f, r * 1.3f,
                  std::fmod(t * 280.0f, 360.0f), 210.0f);
    } else if (a.mood == Mood::Confused && a.badge && s >= 18) {
        float bx = bcx + bw * 0.42f, by = top - s * 0.05f;
        float r = s * 0.17f;
        SolidBrush bub(Color(255, 150, 190, 255));
        g.FillEllipse(&bub, bx - r, by - r, r * 2, r * 2);
        FontFamily ff(L"Segoe UI");
        Font f(&ff, r * 1.5f, FontStyleBold, UnitPixel);
        SolidBrush qb(Color(255, 20, 30, 60));
        StringFormat fmt;
        fmt.SetAlignment(StringAlignmentCenter);
        fmt.SetLineAlignment(StringAlignmentCenter);
        g.DrawString(L"?", 1, &f, RectF(bx - r, by - r, r * 2, r * 2), &fmt, &qb);
    } else if (a.mood == Mood::Waiting && a.badge) {
        float bx = bcx + bw * 0.42f, by = top - s * 0.05f;
        float pulse = 1.0f + 0.08f * std::sin(t * 8.0f);
        float r = s * 0.17f * pulse;
        SolidBrush bub(Color(255, 255, 196, 64));
        g.FillEllipse(&bub, bx - r, by - r, r * 2, r * 2);
        Pen ex(Color(255, 60, 40, 10), std::max(1.0f, s * 0.035f));
        ex.SetStartCap(LineCapRound);
        ex.SetEndCap(LineCapRound);
        g.DrawLine(&ex, bx, by - r * 0.5f, bx, by + r * 0.12f);
        SolidBrush dot(Color(255, 60, 40, 10));
        g.FillEllipse(&dot, bx - s * 0.02f, by + r * 0.35f, s * 0.04f, s * 0.04f);
    } else if (a.mood == Mood::Sleep && s >= 18) {
        float phase = std::fmod(t * 0.6f, 1.0f);
        FontFamily ff(L"Segoe UI");
        Font f(&ff, s * (0.18f + phase * 0.1f), FontStyleBold, UnitPixel);
        SolidBrush zb(Color((BYTE)(200 * (1 - phase)), 200, 230, 210));
        g.DrawString(L"z", 1, &f, PointF(bcx + bw * 0.38f + phase * s * 0.1f, top - phase * s * 0.25f), &zb);
    }
}

HICON MakePetIcon(int px) {
    Bitmap bmp(px, px, PixelFormat32bppARGB);
    {
        Graphics g(&bmp);
        g.SetSmoothingMode(SmoothingModeAntiAlias);
        g.SetPixelOffsetMode(PixelOffsetModeHighQuality);
        g.Clear(Color(0, 0, 0, 0));
        PetAnim a;
        a.mood = Mood::Idle;
        DrawPet(g, px * 0.5f, px * 0.56f, px * 0.72f, a);
    }
    HICON icon = nullptr;
    bmp.GetHICON(&icon);
    return icon;
}
