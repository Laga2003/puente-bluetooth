@echo off
rem Crea dist\PuenteBluetooth.exe. Solo hace falta Python 3.10 o superior en este paso.
py -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
python -m pip install -r requirements.txt || goto :error
pyinstaller --noconfirm --onefile --windowed --name PuenteBluetooth bt_puente.py || goto :error
echo.
echo Listo: dist\PuenteBluetooth.exe
pause
exit /b 0
:error
echo.
echo Ha fallado la compilacion.
pause
exit /b 1
