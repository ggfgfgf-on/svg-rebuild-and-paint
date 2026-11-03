@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set PATH=C:\Program Files\LLVM\bin;%PATH%
clang++ -O3 -mavx2 -std=c++17 -shared "%~dp0paint_core.cpp" "%~dp0plan_core.cpp" -o "%~dp0paint_core.dll"
if errorlevel 1 (echo BUILD FAILED & exit /b 1)
echo BUILD OK
