"""
Weather & Forecast Plugin for SensorsHub & ESP32 Voice Assistant.
Uses the free Open-Meteo Geocoding & Weather API (no API key required).
"""
import json
import re
import urllib.parse
import urllib.request
from typing import Optional

# WMO Weather interpretation codes (WW)
WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}

DEFAULT_CITY = "Cairo"

def get_city_coordinates(city_name: str) -> Optional[tuple[float, float, str, str]]:
    """Fetches latitude, longitude, resolved city name, and country."""
    try:
        encoded_name = urllib.parse.quote(city_name.strip())
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_name}&count=1&language=en&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "SensorsHub/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "results" in data and len(data["results"]) > 0:
                item = data["results"][0]
                lat = item["latitude"]
                lon = item["longitude"]
                name = item.get("name", city_name)
                country = item.get("country", "")
                return lat, lon, name, country
    except Exception as e:
        print(f"[Weather Plugin] Geocoding error: {e}")
    return None

def fetch_weather_report(lat: float, lon: float, city_name: str, country: str) -> Optional[str]:
    """Fetches real-time weather data for given coordinates."""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            "&timezone=auto"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "SensorsHub/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "current" in data:
                current = data["current"]
                temp = round(current.get("temperature_2m", 0))
                humidity = current.get("relative_humidity_2m", 0)
                code = current.get("weather_code", 0)
                wind = round(current.get("wind_speed_10m", 0))
                
                condition = WEATHER_CODES.get(code, "fair weather")
                location_str = f"{city_name}, {country}" if country else city_name
                
                return (
                    f"The current weather in {location_str} is {condition} with a temperature of "
                    f"{temp}°C, {humidity}% humidity, and wind speeds of {wind} km/h."
                )
    except Exception as e:
        print(f"[Weather Plugin] Forecast error: {e}")
    return None

def handle_intent(instruction: str, context: str = "") -> Optional[str]:
    """
    Main plugin entrypoint called by server.py dispatcher.
    Matches weather-related keywords and returns spoken response.
    """
    text = instruction.lower().strip()

    # Trigger patterns: "weather in Cairo", "what's the weather like in Paris", "temperature in Tokyo", "is it raining in London"
    weather_match = re.search(
        r"(?:weather|temperature|forecast|is it raining|how is the weather)(?:\s+(?:in|for|at|like in)\s+([a-zA-Z\s\-]+)|\s*([a-zA-Z\s\-]+)?)",
        text
    )

    if not ("weather" in text or "forecast" in text or "temperature in" in text or "is it raining" in text):
        return None

    target_city = DEFAULT_CITY
    if weather_match:
        city_extracted = weather_match.group(1) or weather_match.group(2)
        if city_extracted:
            cleaned = city_extracted.replace("today", "").replace("now", "").replace("outside", "").replace("like", "").replace("right", "").strip()
            if len(cleaned) > 1:
                target_city = cleaned

    geo = get_city_coordinates(target_city)
    if not geo:
        return f"I could not locate the city '{target_city}' on the weather map. Please check the city name."

    lat, lon, resolved_city, country = geo
    report = fetch_weather_report(lat, lon, resolved_city, country)
    if report:
        return report

    return f"Unable to fetch the current weather forecast for {target_city} right now."
