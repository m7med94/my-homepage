# Mo Project (ESP32-C3 Custom Voice Assistant)

Custom voice assistant board implementation based on ESP32-C3 with ES8311 I2S Audio Codec and SSD1306 OLED display.

## Hardware Features:
- **MCU**: ESP32-C3
- **Audio Codec**: ES8311 (I2S + I2C)
- **Display**: SSD1306 128x64 I2C OLED Display
- **LED**: GPIO 2 (Built-in LED)
- **Button**: GPIO 9 (Boot Button)
- **Network**: Wi-Fi Auto-connect
- **MCP Tools**:
  - `self.led.set_power`
  - `self.screen.set_theme`
  - `self.server.send_data`

## Build & Flash:
```powershell
python scripts/build.py mo-project
```
or run:
```powershell
.\flash.ps1
```
