@echo off
rem Сборка Sprout в Visual Studio 2022 (нужен CMake из состава VS или отдельно)
chcp 65001 >nul
cmake -B build || goto :fail
cmake --build build --config Release || goto :fail
echo.
echo Готово: build\Release\Sprout.exe и build\Release\sprout-hook.exe
echo Положи оба файла в одну папку и запусти Sprout.exe
exit /b 0
:fail
echo.
echo Сборка не удалась. Проверь, что установлены Visual Studio 2022 с C++ и CMake.
exit /b 1
