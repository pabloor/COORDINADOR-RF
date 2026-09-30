@echo off
rem Coordinador RF: doble clic para abrir la app en su propia ventana (de Edge). Al cerrarla se apaga todo.
cd /d "%~dp0"
set PY=
set PYW=
py -3 -c "import sys" >nul 2>nul && (set PY=py -3& set PYW=pyw -3)
if not defined PY python -c "import sys" >nul 2>nul && (set PY=python& set PYW=pythonw)
if not defined PY (
  echo No encuentro Python 3. Instalalo desde https://www.python.org/downloads/ marcando "Add Python to PATH".
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% -c "import serial" >nul 2>nul || (
  echo Preparando la conexion con el analizador USB, solo la primera vez...
  %PY% -m pip install --user --quiet pyserial
)
start "" %PYW% puente-rf.py --ventana --auto-cerrar 90
