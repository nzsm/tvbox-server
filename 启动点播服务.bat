@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  TVBox 点播服务启动中，请稍候...
echo  启动成功后不要关闭本窗口！
echo  停止服务：直接关闭本窗口即可
echo ============================================
python tvbox_server.py
pause
