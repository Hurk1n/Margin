@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安装依赖...
python -m pip install --quiet segno zxingcpp pillow pyinstaller
if errorlevel 1 goto :err
echo [2/2] 打包 Margin.exe ...
python -m PyInstaller --noconfirm --onefile --windowed --name Margin ^
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
