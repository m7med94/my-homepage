# XiaoZhi ESP32-C3 Quick Build & Flash Script
param (
    [string]$Port = "COM5"
)

$ErrorActionPreference = "Stop"

Write-Host ">>> Loading ESP-IDF Environment..." -ForegroundColor Cyan
$env:IDF_TOOLS_PATH = "C:\Espressif"
$env:IDF_PATH = "C:\Espressif\frameworks\esp-idf-v5.5.5"
$env:PATH = "C:\Espressif\python_env\idf5.5_py3.11_env\Scripts;$env:PATH"
& "C:\Espressif\frameworks\esp-idf-v5.5.5\export.ps1" | Out-Null

Write-Host ">>> Compiling mo-project firmware..." -ForegroundColor Cyan
python scripts/build.py mo-project

Write-Host ">>> Flashing to $Port..." -ForegroundColor Green
& "C:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe" -m esptool --chip esp32c3 -p $Port -b 460800 write_flash 0x0 build/merged-binary.bin

Write-Host ">>> Success! Board flashed and rebooted." -ForegroundColor Green
