#include "application.h"
#include "button.h"
#include "codecs/es8311_audio_codec.h"
#include "config.h"
#include "display/display.h"
#include "display/oled_display.h"
#include "mcp_server.h"
#include "power_save_timer.h"
#include "press_to_talk_mcp_tool.h"
#include "system_reset.h"
#include "wifi_board.h"
#include <wifi_manager.h>

#include <driver/gpio.h>
#include <driver/i2c_master.h>
#include <esp_efuse.h>
#include <esp_efuse_table.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_log.h>
#include <ssid_manager.h>

#define TAG "MoProjectBoard"

class MoProjectBoard : public WifiBoard {
private:
    i2c_master_bus_handle_t codec_i2c_bus_;
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    Display* display_ = nullptr;
    Button boot_button_;
    PowerSaveTimer* power_save_timer_ = nullptr;
    PressToTalkMcpTool* press_to_talk_tool_ = nullptr;

    void InitializePowerSaveTimer() {
        power_save_timer_ = new PowerSaveTimer(160, 300);
        power_save_timer_->OnEnterSleepMode([this]() { GetDisplay()->SetPowerSaveMode(true); });
        power_save_timer_->OnExitSleepMode([this]() { GetDisplay()->SetPowerSaveMode(false); });
        power_save_timer_->SetEnabled(true);
    }

    void InitializeCodecI2c() {
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = I2C_NUM_0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags =
                {
                    .enable_internal_pullup = 1,
                },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &codec_i2c_bus_));

        if (!IsEs8311Present()) {
            while (true) {
                ESP_LOGE(
                    TAG,
                    "ES8311 not detected, please check if you have installed the correct firmware");
                vTaskDelay(1000 / portTICK_PERIOD_MS);
            }
        }
    }

    bool IsEs8311Present() {
        i2c_master_dev_handle_t dev = nullptr;
        i2c_device_config_t dev_cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = 0x18,
            .scl_speed_hz = 100 * 1000,
        };
        if (i2c_master_bus_add_device(codec_i2c_bus_, &dev_cfg, &dev) != ESP_OK) {
            return false;
        }

        uint8_t reg = 0xFD;
        uint8_t id1 = 0, id2 = 0;
        esp_err_t err1 = i2c_master_transmit_receive(dev, &reg, 1, &id1, 1, 100);
        reg = 0xFE;
        esp_err_t err2 = i2c_master_transmit_receive(dev, &reg, 1, &id2, 1, 100);
        i2c_master_bus_rm_device(dev);

        ESP_LOGI(TAG, "ES8311 chip id: err=(%s,%s) id=0x%02X 0x%02X", esp_err_to_name(err1),
                 esp_err_to_name(err2), id1, id2);
        return err1 == ESP_OK && err2 == ESP_OK && id1 == 0x83 && id2 == 0x11;
    }

    void InitializeSsd1306Display() {
        esp_lcd_panel_io_i2c_config_t io_config = {};
        io_config.dev_addr = 0x3C;
        io_config.scl_speed_hz = 400 * 1000;
        io_config.control_phase_bytes = 1;
        io_config.dc_bit_offset = 6;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        io_config.on_color_trans_done = nullptr;
        io_config.user_ctx = nullptr;
        io_config.flags.dc_low_on_data = 0;
        io_config.flags.disable_control_phase = 0;

        ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(codec_i2c_bus_, &io_config, &panel_io_));

        ESP_LOGI(TAG, "Install SSD1306 driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = GPIO_NUM_NC;
        panel_config.bits_per_pixel = 1;

        esp_lcd_panel_ssd1306_config_t ssd1306_config = {
            .height = static_cast<uint8_t>(DISPLAY_HEIGHT),
        };
        panel_config.vendor_config = &ssd1306_config;

        ESP_ERROR_CHECK(esp_lcd_new_panel_ssd1306(panel_io_, &panel_config, &panel_));
        ESP_LOGI(TAG, "SSD1306 driver installed");

        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
        if (esp_lcd_panel_init(panel_) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize display");
            display_ = new NoDisplay();
            return;
        }

        ESP_LOGI(TAG, "Turning display on");
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_, true));

        display_ = new OledDisplay(panel_io_, panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT,
                                   DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            if (!press_to_talk_tool_ || !press_to_talk_tool_->IsPressToTalkEnabled()) {
                app.ToggleChatState();
            }
        });
        boot_button_.OnPressDown([this]() {
            if (power_save_timer_) {
                power_save_timer_->WakeUp();
            }
            if (press_to_talk_tool_ && press_to_talk_tool_->IsPressToTalkEnabled()) {
                Application::GetInstance().StartListening();
            }
        });
        boot_button_.OnPressUp([this]() {
            if (press_to_talk_tool_ && press_to_talk_tool_->IsPressToTalkEnabled()) {
                Application::GetInstance().StopListening();
            }
        });
    }

    void InitializeTools() {
        press_to_talk_tool_ = new PressToTalkMcpTool();
        press_to_talk_tool_->Initialize();

        // Built-in LED Hardware Setup (GPIO 2)
        gpio_config_t io_conf = {
            .pin_bit_mask = (1ULL << BUILTIN_LED_GPIO),
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&io_conf);
        gpio_set_level(BUILTIN_LED_GPIO, 0);

        auto& mcp = McpServer::GetInstance();

        // Tool 1: Built-in LED control
        mcp.AddTool("self.led.set_power",
                    "Turn the built-in onboard LED ON or OFF. Set state to true to turn on, or "
                    "false to turn off.",
                    PropertyList({Property("state", kPropertyTypeBoolean)}),
                    [](const PropertyList& properties) -> ReturnValue {
                        bool state = properties["state"].value<bool>();
                        gpio_set_level(BUILTIN_LED_GPIO, state ? 1 : 0);
                        ESP_LOGI(TAG, "Built-in LED set to: %s", state ? "ON" : "OFF");
                        return true;
                    });

        // Tool 2: OLED Screen Theme (Light / Dark mode)
        mcp.AddTool(
            "self.screen.set_theme",
            "Set the display theme of the screen. Supported themes are 'light' (white background "
            "with black content) and 'dark' (black background with white content).",
            PropertyList({Property("theme", kPropertyTypeString)}),
            [this](const PropertyList& properties) -> ReturnValue {
                if (panel_ == nullptr) {
                    return false;
                }
                auto theme = properties["theme"].value<std::string>();
                if (theme == "light" || theme == "inverted" || theme == "day") {
                    esp_lcd_panel_invert_color(panel_, true);
                    ESP_LOGI(TAG, "Screen theme set to: Light/Inverted");
                    return true;
                } else if (theme == "dark" || theme == "normal" || theme == "night") {
                    esp_lcd_panel_invert_color(panel_, false);
                    ESP_LOGI(TAG, "Screen theme set to: Dark/Normal");
                    return true;
                }
                return false;
            });

        // Tool 3: Universal Server Agent Dispatcher (Delegates all heavy logic, to-dos, music, questions to server)
        mcp.AddTool(
            "self.server.dispatch",
            "Send any task, to-do item, reminder, music request, sensor query, question, or general instruction to the central server agent to execute.",
            PropertyList({
                Property("instruction", kPropertyTypeString),
                Property("context", kPropertyTypeString, std::string("general"))
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                std::string instruction = properties["instruction"].value<std::string>();
                std::string context = properties["context"].value<std::string>();
                return DispatchInstructionToServer(instruction, context);
            });

        // Tool 4: Play music or playlist from the server music vault
        mcp.AddTool(
            "self.music.play",
            "Search and play a music track or playlist from the server music vault. "
            "Use this whenever the user asks to play music, play a song, play a playlist (e.g. 'favorites'), or play something random.",
            PropertyList({
                Property("query", kPropertyTypeString, std::string("random"))
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                std::string query = properties["query"].value<std::string>();
                return ResolveVoiceMusicAction(query, "play");
            });

        // Tool 5: List available music & playlists in the server vault
        mcp.AddTool(
            "self.music.list",
            "List available music tracks and playlists stored on the server. "
            "Use this whenever the user asks 'what music do you have', 'what songs do I have', or 'list my playlists'.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                return ResolveVoiceMusicAction("", "list");
            });

        // Tool 6: Get To-Do list & pending tasks from the server
        mcp.AddTool(
            "self.todo.get_list",
            "Fetch and speak the user's current to-do list, pending action items, and tasks from the server.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                return DispatchInstructionToServer("what is my todo list", "todo");
            });

        // Tool 7: Add a task to the To-Do list on the server
        mcp.AddTool(
            "self.todo.add_item",
            "Add a new task, item, or reminder to the user's to-do list on the server.",
            PropertyList({
                Property("text", kPropertyTypeString),
                Property("priority", kPropertyTypeString, std::string("normal"))
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                std::string text = properties["text"].value<std::string>();
                std::string priority = properties["priority"].value<std::string>();
                return DispatchInstructionToServer("add " + text + " with " + priority + " priority", "todo");
            });

        // Tool 8: Delete a task from the To-Do list on the server
        mcp.AddTool(
            "self.todo.delete_item",
            "Delete or remove a task or item from the user's to-do list on the server.",
            PropertyList({
                Property("text", kPropertyTypeString)
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                std::string text = properties["text"].value<std::string>();
                return DispatchInstructionToServer("delete " + text, "todo");
            });

        // Tool 9: Send / Read generic telemetry data
        mcp.AddTool(
            "self.server.send_data",
            "Send custom data, logs, alerts, or events to the external server backend.",
            PropertyList({
                Property("data", kPropertyTypeString),
                Property("category", kPropertyTypeString, std::string("general"))
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                std::string data = properties["data"].value<std::string>();
                std::string category = properties["category"].value<std::string>();
                bool success = SendDataToServer(data, category);
                return success ? "Data successfully sent to server." : "Failed to connect to backend server.";
            });

        // Tool 10: Check server & ESP32 connection status
        mcp.AddTool(
            "self.server.check_connection",
            "Check if the ESP32 is connected to the server, verify server health, or say hello. "
            "Use this whenever the user asks 'is the esp32 connected', 'are you connected', 'check connection', 'is my server healthy', or asks to check connection.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                return DispatchInstructionToServer("is the ESP32 connected?", "esp32");
            });
    }

    std::string DispatchInstructionToServer(const std::string& instruction, const std::string& context = "general") {
        auto network = GetNetwork();
        if (!network) {
            ESP_LOGE(TAG, "Network not ready, cannot reach server agent");
            return "Network connection unavailable.";
        }

        auto http = network->CreateHttp(3);
        if (!http) {
            ESP_LOGE(TAG, "Failed to create HTTP client for agent dispatch");
            return "Internal HTTP client error.";
        }

        cJSON* root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "instruction", instruction.c_str());
        cJSON_AddStringToObject(root, "device_id", "mo-project-c3");
        cJSON_AddStringToObject(root, "context", context.c_str());
        char* post_data = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);

        if (!post_data) {
            return "Error formatting agent request.";
        }

        http->SetTimeout(BACKEND_SERVER_TIMEOUT_MS);
        http->SetHeader("Content-Type", "application/json");
        http->SetContent(std::string(post_data));
        free(post_data);

        ESP_LOGI(TAG, "Dispatching instruction to server agent: %s", BACKEND_AGENT_URL);
        if (!http->Open("POST", BACKEND_AGENT_URL)) {
            ESP_LOGE(TAG, "Failed to connect to agent at %s", BACKEND_AGENT_URL);
            return "Could not connect to the central server agent.";
        }

        int status_code = http->GetStatusCode();
        if (status_code < 200 || status_code >= 300) {
            ESP_LOGE(TAG, "Agent server returned HTTP error: %d", status_code);
            http->Close();
            return "Server agent encountered an error (code " + std::to_string(status_code) + ").";
        }

        std::string response_body = http->ReadAll();
        http->Close();

        if (response_body.empty()) {
            return "Server agent returned an empty response.";
        }

        cJSON* resp_json = cJSON_Parse(response_body.c_str());
        if (resp_json) {
            cJSON* reply = cJSON_GetObjectItem(resp_json, "reply");
            if (cJSON_IsString(reply) && reply->valuestring != nullptr) {
                std::string reply_str = reply->valuestring;
                cJSON_Delete(resp_json);
                return reply_str;
            }
            cJSON* summary = cJSON_GetObjectItem(resp_json, "summary");
            if (cJSON_IsString(summary) && summary->valuestring != nullptr) {
                std::string summary_str = summary->valuestring;
                cJSON_Delete(resp_json);
                return summary_str;
            }
            cJSON_Delete(resp_json);
        }

        return response_body;
    }

    bool SendDataToServer(const std::string& data, const std::string& category) {
        auto network = GetNetwork();
        if (!network) {
            ESP_LOGE(TAG, "Network not ready, cannot send data to server");
            return false;
        }

        auto http = network->CreateHttp(3);
        if (!http) {
            ESP_LOGE(TAG, "Failed to create HTTP client");
            return false;
        }

        cJSON* root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "device_id", "mo-project-c3");
        cJSON_AddStringToObject(root, "category", category.c_str());
        cJSON_AddStringToObject(root, "data", data.c_str());
        char* post_data = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);

        if (!post_data) {
            return false;
        }

        http->SetTimeout(BACKEND_SERVER_TIMEOUT_MS);
        http->SetHeader("Content-Type", "application/json");
        http->SetContent(std::string(post_data));
        free(post_data);

        ESP_LOGI(TAG, "Sending POST request to: %s", BACKEND_SERVER_URL);
        if (!http->Open("POST", BACKEND_SERVER_URL)) {
            ESP_LOGE(TAG, "Failed to open HTTP connection to %s", BACKEND_SERVER_URL);
            return false;
        }

        int status_code = http->GetStatusCode();
        http->Close();

        ESP_LOGI(TAG, "Backend server response code: %d", status_code);
        return (status_code >= 200 && status_code < 300);
    }

    std::string ReadDataFromServer(const std::string& category) {
        auto network = GetNetwork();
        if (!network) {
            ESP_LOGE(TAG, "Network not ready, cannot fetch data from server");
            return "Network connection unavailable.";
        }

        auto http = network->CreateHttp(3);
        if (!http) {
            ESP_LOGE(TAG, "Failed to create HTTP client");
            return "Internal HTTP error.";
        }

        std::string url = BACKEND_SERVER_URL;
        if (!category.empty() && category != "all") {
            url += "?category=" + category + "&limit=1";
        } else {
            url += "?limit=1";
        }

        http->SetTimeout(BACKEND_SERVER_TIMEOUT_MS);
        ESP_LOGI(TAG, "Sending GET request to: %s", url.c_str());

        if (!http->Open("GET", url)) {
            ESP_LOGE(TAG, "Failed to connect to %s", url.c_str());
            return "Failed to connect to backend server.";
        }

        int status_code = http->GetStatusCode();
        if (status_code < 200 || status_code >= 300) {
            ESP_LOGE(TAG, "Backend returned error status: %d", status_code);
            http->Close();
            return "Backend server returned error code " + std::to_string(status_code);
        }

        std::string response_body = http->ReadAll();
        http->Close();

        if (response_body.empty()) {
            return "No data returned from server.";
        }

        // Try extracting summary field if present
        cJSON* root = cJSON_Parse(response_body.c_str());
        if (root) {
            cJSON* summary = cJSON_GetObjectItem(root, "summary");
            if (cJSON_IsString(summary) && summary->valuestring != nullptr) {
                std::string summary_text = summary->valuestring;
                cJSON_Delete(root);
                return summary_text;
            }
            cJSON_Delete(root);
        }

        return response_body;
    }

    std::string ResolveVoiceMusicAction(const std::string& query, const std::string& action) {
        auto network = GetNetwork();
        if (!network) {
            return "Network connection unavailable.";
        }

        auto http = network->CreateHttp(3);
        if (!http) {
            return "Internal HTTP error.";
        }

        std::string url = BACKEND_MUSIC_VOICE_URL;
        url += "?action=" + action;
        if (!query.empty()) {
            url += "&query=" + query;
        }

        http->SetTimeout(BACKEND_SERVER_TIMEOUT_MS);
        ESP_LOGI(TAG, "Resolving voice music action via: %s", url.c_str());

        if (!http->Open("GET", url)) {
            ESP_LOGE(TAG, "Failed to connect to music voice endpoint at %s", url.c_str());
            return "Could not connect to the music server.";
        }

        int status_code = http->GetStatusCode();
        if (status_code < 200 || status_code >= 300) {
            ESP_LOGE(TAG, "Server returned error code %d for music", status_code);
            http->Close();
            return "Failed to fetch music information from server.";
        }

        std::string response_body = http->ReadAll();
        http->Close();

        if (response_body.empty()) {
            return "No response from music server.";
        }

        cJSON* root = cJSON_Parse(response_body.c_str());
        if (root) {
            // Check if there is an audio stream URL to play through the speaker
            cJSON* action_item = cJSON_GetObjectItem(root, "action");
            if (action_item && cJSON_IsString(action_item)) {
                std::string act = action_item->valuestring;
                if (act == "play" || act == "play_playlist") {
                    cJSON* esp_url = cJSON_GetObjectItem(root, "esp32_url");
                    if (!esp_url) esp_url = cJSON_GetObjectItem(root, "url");
                    if (esp_url && cJSON_IsString(esp_url) && esp_url->valuestring != nullptr) {
                        PlayMusicTrackInBackground(esp_url->valuestring);
                    }
                }
            }

            cJSON* voice_summary = cJSON_GetObjectItem(root, "voice_summary");
            if (cJSON_IsString(voice_summary) && voice_summary->valuestring != nullptr) {
                std::string summary_text = voice_summary->valuestring;
                cJSON_Delete(root);
                return summary_text;
            }
            cJSON* summary = cJSON_GetObjectItem(root, "summary");
            if (cJSON_IsString(summary) && summary->valuestring != nullptr) {
                std::string summary_text = summary->valuestring;
                cJSON_Delete(root);
                return summary_text;
            }
            cJSON* track = cJSON_GetObjectItem(root, "track");
            if (track) {
                cJSON* title = cJSON_GetObjectItem(track, "title");
                if (cJSON_IsString(title) && title->valuestring != nullptr) {
                    std::string res = "Playing " + std::string(title->valuestring);
                    cJSON_Delete(root);
                    return res;
                }
            }
            cJSON_Delete(root);
        }

        return response_body;
    }

    void PlayMusicTrackInBackground(const std::string& audio_url) {
        std::string full_url = audio_url;
        if (full_url.rfind("http", 0) != 0) {
            full_url = std::string(BACKEND_BASE_URL) + full_url;
        }

        // Spawn detached task to download OGG Opus audio and pipe to speaker
        std::thread([this, full_url]() {
            // Wait for TTS spoken response to finish
            vTaskDelay(pdMS_TO_TICKS(1800));

            auto network = GetNetwork();
            if (!network) return;

            auto http = network->CreateHttp(3);
            if (!http) return;

            http->SetTimeout(20000);
            ESP_LOGI(TAG, "Fetching OGG Opus audio stream from: %s", full_url.c_str());
            if (!http->Open("GET", full_url)) {
                ESP_LOGE(TAG, "Failed to open audio connection to %s", full_url.c_str());
                return;
            }

            int status_code = http->GetStatusCode();
            if (status_code >= 200 && status_code < 300) {
                std::string ogg_payload = http->ReadAll();
                http->Close();

                if (!ogg_payload.empty()) {
                    ESP_LOGI(TAG, "Downloaded %d bytes of OGG Opus music. Streaming to speaker...", (int)ogg_payload.size());
                    Application::GetInstance().PlaySound(ogg_payload);
                } else {
                    ESP_LOGW(TAG, "Downloaded audio payload was empty.");
                }
            } else {
                ESP_LOGE(TAG, "Audio stream HTTP error: %d", status_code);
                http->Close();
            }
        }).detach();
    }

public:
    MoProjectBoard() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeCodecI2c();
        InitializeSsd1306Display();
        InitializeButtons();
        InitializePowerSaveTimer();
        InitializeTools();

#ifdef DEFAULT_WIFI_SSID
        // Auto-configure default Wi-Fi credentials
        auto& ssid_mgr = SsidManager::GetInstance();
        ESP_LOGI(TAG, "Ensuring default Wi-Fi SSID is configured: %s", DEFAULT_WIFI_SSID);
        ssid_mgr.AddSsid(DEFAULT_WIFI_SSID, DEFAULT_WIFI_PASSWORD);
#endif

        // ESP32-C3 VDD SPI pin config (check if already burned before writing)
        if (esp_efuse_read_field_bit(ESP_EFUSE_VDD_SPI_AS_GPIO) == 0) {
            ESP_LOGI(TAG, "Configuring VDD_SPI as GPIO via eFuse...");
            esp_efuse_write_field_bit(ESP_EFUSE_VDD_SPI_AS_GPIO);
        }
    }

    virtual AudioCodec* GetAudioCodec() override {
        static Es8311AudioCodec audio_codec(
            codec_i2c_bus_, I2C_NUM_0, AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK, AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS, AUDIO_I2S_GPIO_DOUT,
            AUDIO_I2S_GPIO_DIN, AUDIO_CODEC_PA_PIN, AUDIO_CODEC_ES8311_ADDR);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override { return display_; }
};

DECLARE_BOARD(MoProjectBoard);
