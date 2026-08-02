"""Example Weather plugin."""

from __future__ import annotations

from core.plugins.base import Plugin


def weather_lookup(city: str = "Johannesburg") -> dict:
    # Demo data — real providers can replace this
    demo = {
        "johannesburg": {"temp_c": 18, "condition": "Partly cloudy"},
        "cape town": {"temp_c": 16, "condition": "Windy"},
        "durban": {"temp_c": 24, "condition": "Humid"},
    }
    key = (city or "").strip().lower()
    info = demo.get(key, {"temp_c": 20, "condition": "Clear (demo)"})
    return {"ok": True, "city": city, **info, "message": f"{city}: {info['temp_c']}°C, {info['condition']}"}


class PluginImpl(Plugin):
    def load(self, api):
        api.register_tool(
            "weather_lookup",
            weather_lookup,
            description="Get demo weather for a city",
            permission="weather_lookup",
            tags=["weather", "demo"],
        )

        def cmd(arg: str) -> str:
            city = arg.strip() or "Johannesburg"
            r = weather_lookup(city)
            return r.get("message", str(r))

        api.register_command("weather", cmd)
        api.log("Weather plugin loaded")
