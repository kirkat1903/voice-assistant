// GDI+ ждёт макросы min/max, а мы собираем с NOMINMAX.
#pragma once
#include "../common/shared.h"
#include <algorithm>
namespace Gdiplus {
using std::max;
using std::min;
}
#include <objidl.h>
#include <gdiplus.h>
