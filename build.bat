@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 准备独立打包环境...
set "MARGIN_BUILD_PY=%cd%\build\.venv\Scripts\python.exe"
if not exist "%MARGIN_BUILD_PY%" python -m venv build\.venv
if errorlevel 1 goto :err
echo [2/3] 安装依赖...
"%MARGIN_BUILD_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :err
echo [3/3] 打包 Margin.exe 和 MarginCLI.exe ...
"%MARGIN_BUILD_PY%" -m PyInstaller --noconfirm --onefile --windowed --name Margin ^
  --icon "%cd%\assets\margin.ico" --add-data "%cd%\assets;assets" ^
  --collect-all zxingcpp --hidden-import PIL._tkinter_finder ^
  --workpath build --distpath dist --specpath build app.py
if errorlevel 1 goto :err
"%MARGIN_BUILD_PY%" -m PyInstaller --noconfirm --onefile --console --name MarginCLI ^
  --icon "%cd%\assets\margin.ico" --add-data "%cd%\assets;assets" ^
  --collect-all zxingcpp --hidden-import PIL._tkinter_finder ^
  --workpath build --distpath dist --specpath build app.py
if errorlevel 1 goto :err
echo.
echo 完成: %cd%\dist\Margin.exe
pause
exit /b 0
:err
echo 打包失败，请检查上面的输出。
pause
exit /b 1
