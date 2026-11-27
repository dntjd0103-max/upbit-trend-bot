@echo off
chcp 65001 > nul
cd /d "%~dp0"
set SERVER=YOUR_SERVER_IP
set KEY=
for %%k in (*.pem) do if not defined KEY set "KEY=%%k"
if not defined KEY (
  echo [!] .pem key file not found in this folder.
  echo     Lightsail - Account - SSH keys - Default key ^(Sydney^) - Download, then put it in this folder.
  pause
  exit /b
)
if not exist .env ( echo [!] .env not found & pause & exit /b )
echo Building upbit_server.zip from the files in this folder (your config.py included)...
python make_server_zip.py
if errorlevel 1 ( echo [!] Could not build the zip. & pause & exit /b )
echo Using key: %KEY%   Server: %SERVER%
rem --- key file must be readable only by you, or ssh refuses it
icacls "%KEY%" /inheritance:r > nul
icacls "%KEY%" /grant:r "%USERNAME%:R" > nul
echo.
echo [1/2] Uploading files to the server...
scp -i "%KEY%" -o StrictHostKeyChecking=accept-new upbit_server.zip .env ubuntu@%SERVER%:~/
if errorlevel 1 ( echo [!] Upload failed. & pause & exit /b )
echo.
echo [2/2] Installing on the server (3-5 minutes)...
ssh -t -i "%KEY%" -o StrictHostKeyChecking=accept-new ubuntu@%SERVER% "python3 -m zipfile -e upbit_server.zip . && mv -f .env upbit_ai_trader/.env && bash upbit_ai_trader/install.sh"
echo.
echo Done. Check the result above.
pause
