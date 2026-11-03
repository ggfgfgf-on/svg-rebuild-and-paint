@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set PATH=C:\Program Files\LLVM\bin;%PATH%
clang++ -O2 -std=c++17 "%~dp0paint_gpu.cpp" -o "%~dp0paint_gpu.dll" -shared -ld3d11 -ld3dcompiler -ldxgi
if errorlevel 1 (echo BUILD FAIL & exit /b 1)
echo BUILD OK
