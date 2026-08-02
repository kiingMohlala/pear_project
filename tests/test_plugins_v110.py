"""Plugin SDK regression tests (v1.10)."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.plugins import PluginManager, PluginManifest, version_compatible
from core.plugins.api import PluginAPI
from agents import PersonalAgent


def test_version_compatible():
    assert version_compatible(">=1.0", "1.10")
    assert version_compatible(">=2.0", "1.10") is False
    assert version_compatible("==1.10", "1.10")


def test_discover_builtin_plugins():
    orch = Orchestrator(memory=Memory(session_id="p1"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    pm = PluginManager(orch, plugins_dir=ROOT / "plugins")
    found = pm.discover()
    names = {r.manifest.name for r in found}
    assert "weather" in names
    assert "notion" in names
    assert "slack" in names


def test_load_weather_registers_tool_and_command():
    orch = Orchestrator(memory=Memory(session_id="p2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    pm = PluginManager(orch, plugins_dir=ROOT / "plugins")
    pm.discover()
    pm.enable("weather")
    assert orch.registry.has("weather_lookup")
    assert "weather" in orch.plugin_commands
    r = orch.registry.call("weather_lookup", "Johannesburg")
    assert r.get("ok")
    msg = orch.plugin_commands["weather"]("Cape Town")
    assert "Cape Town" in msg or "°C" in msg


def test_permission_enforcement():
    orch = Orchestrator(memory=Memory(session_id="p3"), llm=EchoLLM())
    manifest = PluginManifest(name="locked", permissions=[])  # no perms
    api = PluginAPI(orch, manifest)
    try:
        api.register_tool("x", lambda: None)
        assert False, "should raise"
    except PermissionError:
        pass


def test_disable_enable():
    orch = Orchestrator(memory=Memory(session_id="p4"), llm=EchoLLM())
    pm = PluginManager(orch, plugins_dir=ROOT / "plugins")
    pm.discover()
    pm.enable("slack")
    assert pm.plugins["slack"].enabled
    pm.disable("slack")
    assert not pm.plugins["slack"].enabled


def test_checksum_verification():
    with tempfile.TemporaryDirectory() as td:
        plug = Path(td) / "demo"
        plug.mkdir()
        entry = plug / "plugin.py"
        entry.write_text(
            "from core.plugins.base import Plugin\n"
            "class PluginImpl(Plugin):\n"
            "    def load(self, api): pass\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()
        manifest = PluginManifest(
            name="demo",
            entry="plugin.py",
            permissions=["register_tool"],
            checksum=digest,
            pear_version=">=1.0",
        )
        manifest.save(plug / "plugin.json")
        orch = Orchestrator(memory=Memory(session_id="p5"), llm=EchoLLM())
        pm = PluginManager(orch, plugins_dir=Path(td))
        pm.discover()
        assert pm.plugins["demo"].checksum_ok
        # tamper
        entry.write_text(entry.read_text() + "\n# tampered\n", encoding="utf-8")
        pm2 = PluginManager(orch, plugins_dir=Path(td))
        pm2.discover()
        assert not pm2.plugins["demo"].checksum_ok


def test_dependency_order():
    with tempfile.TemporaryDirectory() as td:
        def write_plugin(name, deps):
            d = Path(td) / name
            d.mkdir()
            (d / "plugin.py").write_text(
                "from core.plugins.base import Plugin\n"
                "class PluginImpl(Plugin):\n"
                "    def load(self, api): pass\n",
                encoding="utf-8",
            )
            PluginManifest(
                name=name,
                dependencies=deps,
                permissions=[],
                pear_version=">=1.0",
            ).save(d / "plugin.json")

        write_plugin("base_p", [])
        write_plugin("child_p", ["base_p"])
        orch = Orchestrator(memory=Memory(session_id="p6"), llm=EchoLLM())
        pm = PluginManager(orch, plugins_dir=Path(td))
        pm.discover()
        order = pm._resolve_order()
        assert order.index("base_p") < order.index("child_p")


def test_notion_registers_connector_when_enabled():
    orch = Orchestrator(memory=Memory(session_id="p7"), llm=EchoLLM())
    pm = PluginManager(orch, plugins_dir=ROOT / "plugins")
    pm.discover()
    pm.enable("notion")
    assert orch.connectors.has("notion")


def test_failure_isolation():
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad"
        bad.mkdir()
        (bad / "plugin.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        PluginManifest(name="bad", permissions=[], pear_version=">=1.0").save(bad / "plugin.json")
        good = Path(td) / "good"
        good.mkdir()
        (good / "plugin.py").write_text(
            "from core.plugins.base import Plugin\n"
            "class PluginImpl(Plugin):\n"
            "    def load(self, api): pass\n",
            encoding="utf-8",
        )
        PluginManifest(name="good", permissions=[], pear_version=">=1.0").save(good / "plugin.json")
        orch = Orchestrator(memory=Memory(session_id="p8"), llm=EchoLLM())
        pm = PluginManager(orch, plugins_dir=Path(td))
        results = pm.load_all()
        assert "error" in results.get("bad", "")
        # good may load
        assert results.get("good") in ("loaded", "disabled") or "error" not in results.get("good", "error")


if __name__ == "__main__":
    test_version_compatible()
    print("  ✓ version")
    test_discover_builtin_plugins()
    print("  ✓ discover")
    test_load_weather_registers_tool_and_command()
    print("  ✓ weather load")
    test_permission_enforcement()
    print("  ✓ permissions")
    test_disable_enable()
    print("  ✓ enable/disable")
    test_checksum_verification()
    print("  ✓ checksum")
    test_dependency_order()
    print("  ✓ deps")
    test_notion_registers_connector_when_enabled()
    print("  ✓ notion connector")
    test_failure_isolation()
    print("  ✓ isolation")
    print("All v1.10 plugin tests passed.")
