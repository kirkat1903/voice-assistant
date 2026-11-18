cmake_minimum_required(VERSION 3.16)
project(Sprout LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
  message(FATAL_ERROR "Sprout собирается только под Windows (MSVC или MinGW).")
endif()

add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)

if(MSVC)
  add_compile_options(/utf-8 /W3 /EHsc)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")   # без зависимостей от VC++ Redist
else()
  add_compile_options(-Wall -Wno-unknown-pragmas -Wno-cast-function-type)
  add_link_options(-static)
endif()

include_directories(third_party)

# --- SQLite: чтение базы уведомлений Windows (чаты приложения Claude) ---
add_library(sqlite3 STATIC third_party/sqlite/sqlite3.c)
target_compile_definitions(sqlite3 PRIVATE SQLITE_OMIT_LOAD_EXTENSION SQLITE_THREADSAFE=1 SQLITE_DQS=0)
if(NOT MSVC)
  target_compile_options(sqlite3 PRIVATE -w)
endif()

# --- sprout-hook.exe: крошечный консольный мост для хуков Claude Code ---
add_executable(sprout-hook src/hook/hook_main.cpp)
target_link_libraries(sprout-hook PRIVATE advapi32)

# --- Sprout.exe: островок, трей, чат ---
add_executable(Sprout WIN32
  src/app/main.cpp
  src/app/island.cpp
  src/app/pet.cpp
  src/app/sessions.cpp
  src/app/pipe_server.cpp
  src/app/config.cpp
  src/app/installer.cpp
  src/app/sound.cpp
  src/app/chat.cpp
  src/app/http.cpp
  src/app/aiko.cpp
  src/app/chats.cpp
  src/app/sprout.rc
)
if(NOT MSVC)
  target_link_options(Sprout PRIVATE -municode)
endif()
target_link_libraries(Sprout PRIVATE sqlite3 gdiplus winhttp winmm credui advapi32 shell32 ole32 user32 gdi32 comctl32)
