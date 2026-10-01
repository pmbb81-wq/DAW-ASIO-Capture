@echo off
rem AXE I/O ONE -> OBS audio bridge
cd /d "%~dp0"
python axeio_obs_bridge.py --vu --gain -3
pause