"""Unit tests for the decky-independent backend modules.

Run with: python3 -m unittest discover -s tests
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from eclipse import REQUEST_FILE, diagnostics, display, flatpak, host_display, launch, network, proc, updates  # noqa: E402
from eclipse.moonlight import build_stream_args  # noqa: E402
from eclipse.moonlight_config import hosts_from_sections, parse_ini  # noqa: E402
from eclipse.presets import AUTO_PRESET, STREAM_DEFAULTS, all_presets, effective_stream  # noqa: E402
from eclipse.settings import SettingsStore, deep_merge, mask_secrets, strip_masked_fields  # noqa: E402
from eclipse import sunshine  # noqa: E402
from eclipse import sunshine_native  # noqa: E402
from eclipse import sunshine_session  # noqa: E402
from eclipse import account  # noqa: E402
from eclipse import sunshine_data  # noqa: E402
from eclipse import sunshine_config  # noqa: E402
from eclipse import backup, battery, mdns, nettest, sunshine_apps, tailscale  # noqa: E402
from eclipse.settings import DEFAULTS  # noqa: E402

SAMPLE_CONF = r"""
[General]
latestSupportedVersion-v1=@Variant(\0\0\0\x7f\0\0\0\vQVersionNumber\0\0\0\0\x3\0\0\0\x5)

[hosts]
1\apps\1\appcollector=false
1\apps\1\directlaunch=false
1\apps\1\hdr=true
1\apps\1\hidden=false
1\apps\1\id=881448767
1\apps\1\name=Desktop
1\apps\2\hdr=false
1\apps\2\hidden=true
1\apps\2\id=1093255277
1\apps\2\name=Hidden App
1\apps\3\hdr=false
1\apps\3\hidden=false
1\apps\3\id=1234
1\apps\3\name="Game, The Sequel"
1\apps\size=3
1\hostname=GAMING-PC
1\localaddress=192.168.1.20
1\localport=47989
1\mac=@ByteArray(\x1c\x1b\r\x12\x34V)
1\manualaddress=
1\remoteaddress=203.0.113.5
1\remoteport=47989
1\srvcert=@ByteArray(-----BEGIN CERTIFICATE-----\n)
1\uuid=0A1B2C3D-0000-1111-2222-333344445555
2\hostname=NO-UUID
size=2
"""


class MoonlightConfigTests(unittest.TestCase):
    def test_parses_hosts_and_apps(self):
        hosts = hosts_from_sections(parse_ini(SAMPLE_CONF))
        self.assertEqual(len(hosts), 1, "hosts without uuid are skipped")
        host = hosts[0]
        self.assertEqual(host["name"], "GAMING-PC")
        self.assertEqual(host["uuid"], "0A1B2C3D-0000-1111-2222-333344445555")
        self.assertEqual([a["kind"] for a in host["addresses"]], ["local", "remote"])
        self.assertEqual(host["addresses"][0], {"kind": "local", "address": "192.168.1.20", "port": 47989})
        names = [a["name"] for a in host["apps"]]
        self.assertEqual(names, ["Desktop", "Hidden App", "Game, The Sequel"])
        self.assertTrue(host["apps"][0]["hdr"])
        self.assertTrue(host["apps"][1]["hidden"])

    def test_paired_flag_follows_server_certificate(self):
        conf = SAMPLE_CONF.replace(r"1\srvcert=@ByteArray(-----BEGIN CERTIFICATE-----\n)",
                                   r"1\srvcert=@ByteArray(-----BEGIN CERTIFICATE-----\nMIIC" + "A" * 64 + r"\n-----END CERTIFICATE-----\n)")
        self.assertTrue(hosts_from_sections(parse_ini(conf))[0]["paired"])
        unpaired = SAMPLE_CONF.replace(r"1\srvcert=@ByteArray(-----BEGIN CERTIFICATE-----\n)", r"1\srvcert=@ByteArray()")
        self.assertFalse(hosts_from_sections(parse_ini(unpaired))[0]["paired"])

    def test_decodes_mac_bytearray(self):
        host = hosts_from_sections(parse_ini(SAMPLE_CONF))[0]
        # \x1c \x1b \r(0d) \x12 \x34 V(56)
        self.assertEqual(host["mac"], "1c:1b:0d:12:34:56")


class StreamArgsTests(unittest.TestCase):
    def test_defaults_build_expected_flags(self):
        args = build_stream_args(STREAM_DEFAULTS)
        self.assertIn("--resolution", args)
        self.assertEqual(args[args.index("--resolution") + 1], "1280x800")
        self.assertEqual(args[args.index("--fps") + 1], "60")
        self.assertEqual(args[args.index("--bitrate") + 1], "15000")
        self.assertEqual(args[args.index("--video-codec") + 1], "HEVC")
        self.assertIn("--frame-pacing", args)
        self.assertIn("--no-vsync", args)
        self.assertIn("--no-hdr", args)

    def test_invalid_values_are_dropped(self):
        args = build_stream_args({"resolution": "huge", "fps": 9999, "bitrateKbps": 1, "videoCodec": "VP9"})
        self.assertEqual(args, [])

    def test_host_preset_overrides_global(self):
        settings = {"activePreset": "custom", "stream": {"fps": 60, "hdr": True}, "hosts": {"h1": {"preset": "battery"}}}
        stream = effective_stream(settings, "h1")
        self.assertEqual(stream["fps"], 30)
        self.assertTrue(stream["hdr"], "non-preset values stay global")
        self.assertEqual(effective_stream(settings, "other")["fps"], 60)

    def test_saved_profile_applies_globally_and_per_host(self):
        profile = {"name": "Couch", "stream": {"fps": 120, "hdr": True, "bogus": 1}}
        settings = {"activePreset": "p-1", "stream": {"fps": 60}, "profiles": {"p-1": profile}, "hosts": {}}
        stream = effective_stream(settings)
        self.assertEqual((stream["fps"], stream["hdr"]), (120, True))
        self.assertNotIn("bogus", stream)
        settings = {"activePreset": "custom", "stream": {"fps": 60}, "profiles": {"p-1": profile},
                    "hosts": {"h1": {"preset": "p-1"}}}
        self.assertEqual(effective_stream(settings, "h1")["fps"], 120)
        self.assertEqual(effective_stream(settings, "h2")["fps"], 60)
        settings["hosts"]["h1"]["preset"] = "p-deleted"
        self.assertEqual(effective_stream(settings, "h1")["fps"], 60, "a deleted profile falls back")


def dtd(width, height, refresh, h_blank=160, v_blank=40):
    """Encode an 18-byte EDID Detailed Timing Descriptor."""
    clock = round(refresh * (width + h_blank) * (height + v_blank) / 10000)
    return bytes([
        clock & 0xFF, clock >> 8,
        width & 0xFF, h_blank & 0xFF, ((width >> 8) << 4) | (h_blank >> 8),
        height & 0xFF, v_blank & 0xFF, ((height >> 8) << 4) | (v_blank >> 8),
    ]) + bytes(10)


def make_edid(base_timings, cta_timings=()):
    base = bytearray(128)
    base[0:8] = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    for index, timing in enumerate(base_timings[:4]):
        base[54 + index * 18:72 + index * 18] = timing
    if not cta_timings:
        return bytes(base)
    base[126] = 1
    ext = bytearray(128)
    ext[0], ext[1], ext[2] = 0x02, 0x03, 4
    for index, timing in enumerate(cta_timings):
        ext[4 + index * 18:22 + index * 18] = timing
    return bytes(base) + bytes(ext)


class DisplayTests(unittest.TestCase):
    def make_drm(self, root, outputs):
        for name, status, modes, edid in outputs:
            path = os.path.join(root, name)
            os.makedirs(path)
            with open(os.path.join(path, "status"), "w") as fh:
                fh.write(status + "\n")
            with open(os.path.join(path, "modes"), "w") as fh:
                fh.write("\n".join(modes) + "\n")
            with open(os.path.join(path, "edid"), "wb") as fh:
                fh.write(edid)
        os.makedirs(os.path.join(root, "card0"))  # non-connector entries are ignored

    def make_dmi(self, root, product):
        path = os.path.join(root, "product_name")
        with open(path, "w") as fh:
            fh.write(product + "\n")
        return path

    def test_edid_timing_decoding(self):
        timings = display.parse_edid(make_edid([dtd(800, 1280, 90), dtd(800, 1280, 60)]))
        self.assertEqual([(t["width"], t["height"]) for t in timings], [(800, 1280), (800, 1280)])
        self.assertAlmostEqual(timings[0]["refreshHz"], 90, delta=0.1)
        self.assertEqual(display.parse_edid(b"garbage"), [])

    def test_portrait_panel_uses_best_native_refresh(self):
        # Any handheld: native portrait panel, preferred 60 Hz, 120 Hz also listed.
        edid = make_edid([dtd(1200, 1920, 60), dtd(1200, 1920, 120), dtd(800, 1280, 144)])
        timing = display.native_timing(edid)
        self.assertEqual(timing, {"width": 1920, "height": 1200, "refreshHz": 120})

    def test_high_refresh_monitor_from_cta_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            drm = os.path.join(tmp, "drm")
            monitor = make_edid([dtd(2560, 1440, 60)], [dtd(2560, 1440, 144), dtd(1920, 1080, 240)])
            self.make_drm(drm, [("card1-HDMI-A-1", "connected", ["2560x1440"], monitor)])
            info = display.detect(drm, self.make_dmi(tmp, "Some Desktop"))
            self.assertEqual((info["resolution"], info["refreshHz"]), ("2560x1440", 144))
            self.assertEqual(info["refreshSource"], "edid")
            self.assertTrue(info["external"])

    def test_external_display_wins_over_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            drm = os.path.join(tmp, "drm")
            self.make_drm(drm, [
                ("card0-eDP-1", "connected", ["800x1280"], make_edid([dtd(800, 1280, 90)])),
                ("card0-DP-1", "connected", ["3840x2160", "1920x1080"], make_edid([dtd(3840, 2160, 60)])),
            ])
            info = display.detect(drm, self.make_dmi(tmp, "Galileo"))
            self.assertEqual((info["resolution"], info["refreshHz"], info["output"]), ("3840x2160", 60, "DP-1"))

    def test_panel_without_edid_uses_device_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            drm = os.path.join(tmp, "drm")
            self.make_drm(drm, [("card0-eDP-1", "connected", ["800x1280"], b"")])
            oled = display.detect(drm, self.make_dmi(tmp, "Galileo"))
            self.assertEqual((oled["resolution"], oled["refreshHz"], oled["refreshSource"]), ("1280x800", 90, "device"))
            unknown = display.detect(drm, self.make_dmi(tmp, "Unknown Handheld"))
            self.assertEqual((unknown["refreshHz"], unknown["refreshSource"]), (60, "default"))

    def test_nothing_detected_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = display.detect(os.path.join(tmp, "missing"), os.path.join(tmp, "missing"))
            self.assertEqual((info["resolution"], info["refreshSource"]), ("1280x800", "default"))

    def test_bitrate_scale(self):
        self.assertEqual(display.recommended_bitrate(1920, 1080, 60), 30000)
        self.assertEqual(display.recommended_bitrate(1280, 800, 60), 17500)
        self.assertLessEqual(display.recommended_bitrate(7680, 4320, 120), 150000)

    def test_auto_preset_uses_display(self):
        info = {"width": 1920, "height": 1080, "resolution": "1920x1080", "refreshHz": 60, "external": True}
        stream = effective_stream({"activePreset": AUTO_PRESET, "stream": {"fps": 30}}, None, info)
        self.assertEqual((stream["resolution"], stream["fps"], stream["bitrateKbps"]), ("1920x1080", 60, 30000))
        self.assertEqual(all_presets(info)[0]["id"], AUTO_PRESET)
        # A host pinned to a fixed preset ignores Auto.
        pinned = {"activePreset": AUTO_PRESET, "hosts": {"h": {"preset": "battery"}}}
        self.assertEqual(effective_stream(pinned, "h", info)["fps"], 30)


KMS_LOG = """
[2026-10-01 10:00:00.000]: Info: -------- Start of KMS monitor list --------
[2026-10-01 10:00:00.001]: Info: Monitor 0 is DP-1: LG ULTRAGEAR
[2026-10-01 10:00:00.002]: Info: Monitor 1 is HDMI-A-1: Samsung TV
[2026-10-01 10:00:00.003]: Info: --------- End of KMS monitor list ---------
"""

WINDOWS_LOG = r"""[2026-10-01 10:00:00.000]: Info: Currently available display devices:
[
  {
    "device_id": "{64243705-4020-5895-b923-adc862c3457e}",
    "display_name": "\\\\.\\DISPLAY1",
    "friendly_name": "DELL P2317H",
    "info": null
  },
  {
    "device_id": "{77f67f3e-754f-5d31-af64-ee037e18100a}",
    "display_name": "",
    "friendly_name": "Virtual Display"
  }
]
[2026-10-01 10:00:01.000]: Info: Next line
"""


class FakeSunshine:
    """Stands in for SunshineApi.request_sync and records writes."""

    def __init__(self, config):
        self.config = dict(config)
        self.posts = []

    def request_sync(self, method, path, body, timeout, limit=0, raw_text=False):
        if (method, path) == ("GET", "/api/config"):
            return 200, {"status": True, "platform": "linux", "version": "2026.1", **self.config}
        if (method, path) == ("POST", "/api/config"):
            self.posts.append(body)
            self.config = dict(body)
            return 200, {"status": True}
        if (method, path) == ("POST", "/api/restart"):
            return 0, None
        if (method, path) == ("GET", "/api/logs"):
            return 200, KMS_LOG
        raise AssertionError((method, path))


class HostDisplayTests(unittest.TestCase):
    def setUp(self):
        self._probe = network.probe_sync
        self._wait = host_display._wait_restart
        self.busy = False
        network.probe_sync = lambda *a, **k: {"online": True, "busy": self.busy}
        host_display._wait_restart = lambda addresses, timeout=40.0: True

    def tearDown(self):
        network.probe_sync = self._probe
        host_display._wait_restart = self._wait

    def test_parse_kms_log(self):
        displays = host_display.parse_displays(KMS_LOG)
        self.assertEqual([d["outputName"] for d in displays], ["DP-1", "HDMI-A-1"])
        self.assertEqual(displays[1]["label"], "HDMI-A-1 (Samsung TV)")

    def test_only_latest_kms_enumeration_counts(self):
        log = KMS_LOG + (
            "[2026-10-02 08:00:00.000]: Info: -------- Start of KMS monitor list --------\n"
            "[2026-10-02 08:00:00.001]: Info: Monitor 0 is DP-1: LG ULTRAGEAR\n"
            "[2026-10-02 08:00:00.002]: Info: --------- End of KMS monitor list ---------\n"
            "[2026-10-02 08:00:05.000]: Info: Monitor 9 is HDMI-A-9: unrelated line after the block\n"
        )
        displays, detected_at = host_display.parse_displays_with_time(log)
        self.assertEqual([d["outputName"] for d in displays], ["DP-1"], "the unplugged TV must not linger")
        self.assertEqual(detected_at, "2026-10-02 08:00:00")

    def test_stream_blocked_when_selected_monitor_is_gone(self):
        original = host_display.SunshineApi
        host_display.SunshineApi = lambda *a, **k: FakeSunshine({"output_name": "DP-1"})
        try:
            host = {"uuid": "u", "name": "PC", "addresses": [{"address": "h", "port": 47989}]}
            settings = {"hosts": {"u": {
                "display": "HDMI-A-2", "sunshineUser": "a", "sunshinePassword": "b",
                "displays": [{"outputName": "HDMI-A-2", "label": "Old TV"}],
            }}}
            result = host_display.apply_host_selection(settings, host, "/nonexistent")
            self.assertFalse(result["ok"])
            self.assertTrue(result["missing"])
            self.assertIn("Old TV", result["error"])
            settings["hosts"]["u"]["display"] = "HDMI-A-1"  # present in KMS_LOG
            with tempfile.TemporaryDirectory() as tmp:
                ok = host_display.apply_host_selection(settings, host, tmp)
            self.assertEqual((ok["ok"], ok["changed"]), (True, True))
        finally:
            host_display.SunshineApi = original

    def test_parse_windows_log(self):
        displays = host_display.parse_displays(WINDOWS_LOG)
        self.assertEqual(displays[0]["outputName"], "{64243705-4020-5895-b923-adc862c3457e}")
        self.assertIn("DELL P2317H", displays[0]["label"])
        self.assertEqual(displays[1]["label"], "Virtual Display")

    def test_switch_preserves_other_settings_and_backs_up(self):
        api = FakeSunshine({"sunshine_name": "PC", "encoder": "nvenc", "output_name": "DP-1"})
        with tempfile.TemporaryDirectory() as tmp:
            backup = os.path.join(tmp, "backup.json")
            result = host_display.switch_display(api, "HDMI-A-1", [{"address": "h", "port": 47989}], backup)
            self.assertEqual(result, {"ok": True, "changed": True, "error": None})
            self.assertEqual(api.posts[-1], {"sunshine_name": "PC", "encoder": "nvenc", "output_name": "HDMI-A-1"})
            with open(backup) as fh:
                self.assertEqual(json.load(fh)["output_name"], "DP-1")

    def test_current_output(self):
        self.assertEqual(host_display.current_output(FakeSunshine({"output_name": "HDMI-A-1"}))["current"], "HDMI-A-1")
        self.assertEqual(host_display.current_output(FakeSunshine({}))["current"], "", "empty means Sunshine's default")

    def test_switch_is_noop_when_unchanged(self):
        api = FakeSunshine({"output_name": "DP-1"})
        result = host_display.switch_display(api, "DP-1", [{"address": "h", "port": 47989}])
        self.assertFalse(result["changed"])
        self.assertEqual(api.posts, [])

    def test_switch_refuses_when_host_busy(self):
        self.busy = True
        api = FakeSunshine({"output_name": "DP-1"})
        result = host_display.switch_display(api, "HDMI-A-1", [{"address": "h", "port": 47989}])
        self.assertFalse(result["ok"])
        self.assertEqual(api.posts, [])

    def test_no_selection_leaves_sunshine_alone(self):
        host = {"uuid": "u", "name": "PC", "addresses": [{"address": "h", "port": 47989}]}
        result = host_display.apply_host_selection({"hosts": {"u": {}}}, host, "/nonexistent")
        self.assertEqual(result, {"ok": True, "changed": False, "error": None})
        missing_creds = host_display.apply_host_selection({"hosts": {"u": {"display": "DP-1"}}}, host, "/nonexistent")
        self.assertFalse(missing_creds["ok"])


class ProcTests(unittest.TestCase):
    def test_cancel_kills_process_group(self):
        lines = []

        async def scenario():
            # The shell starts a child; both must die when the task is cancelled.
            task = asyncio.ensure_future(proc.run_streaming(
                ["sh", "-c", "echo started; sleep 30 & wait"], lines.append, timeout=60,
            ))
            await asyncio.sleep(0.3)
            started = time.monotonic()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return time.monotonic() - started

        elapsed = asyncio.run(scenario())
        self.assertEqual(lines, ["started"])
        self.assertLess(elapsed, 3, "cancellation must not wait for the command")

    def test_carriage_return_progress_is_split(self):
        lines = []
        rc = asyncio.run(proc.run_streaming(["printf", "%b", "a 10%\\rb 50%\\nend\\n"], lines.append))
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ["a 10%", "b 50%", "end"])


class UpdateTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(updates.is_newer("v0.2.0", "0.1.9"))
        self.assertTrue(updates.is_newer("1.10.0", "1.9.3"))
        self.assertFalse(updates.is_newer("v1.0", "1.0.0"))
        self.assertFalse(updates.is_newer("0.1.0-beta", "0.1.0"))

    def test_parse_repository(self):
        self.assertEqual(updates.parse_repository("github:me/DeckyEclipse"), ("me", "DeckyEclipse"))
        self.assertEqual(updates.parse_repository({"url": "https://github.com/me/repo.git"}), ("me", "repo"))
        self.assertEqual(updates.parse_repository("git@github.com:me/repo.git"), ("me", "repo"))
        self.assertIsNone(updates.parse_repository("https://gitlab.com/me/repo"))

    def test_missing_repository_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"version": "0.1.0"}, fh)
            result = updates.check(tmp)
            self.assertFalse(result["ok"])
            self.assertIn("repository", result["error"])


class FlatpakTests(unittest.TestCase):
    def setUp(self):
        self._run = proc.run
        self.calls = []
        self.output = ""

        async def fake_run(cmd, timeout=10.0, home=None):
            self.calls.append(cmd)
            return 0, self.output, ""

        proc.run = fake_run

    def tearDown(self):
        proc.run = self._run

    def test_app_info_reads_columns(self):
        self.output = (
            "com.moonlight_stream.Moonlight\t6.1.0\tsystem\tflathub\tstable\n"
            "dev.lizardbyte.app.Sunshine\t2025.122.141614\tuser\tflathub\tstable\n"
        )
        info = asyncio.run(flatpak.app_info("dev.lizardbyte.app.Sunshine"))
        self.assertEqual(info, {"version": "2025.122.141614", "installation": "user", "origin": "flathub", "branch": "stable"})
        self.assertIsNone(asyncio.run(flatpak.app_info("org.example.Missing")))

    def test_update_check_uses_installation_scope(self):
        self.output = "com.moonlight_stream.Moonlight\t6.2.0\n"
        result = asyncio.run(flatpak.check_update("com.moonlight_stream.Moonlight", "system"))
        self.assertEqual((result["available"], result["version"]), (True, "6.2.0"))
        self.assertIn("--system", self.calls[-1])
        self.output = ""
        self.assertFalse(asyncio.run(flatpak.check_update("com.moonlight_stream.Moonlight", "user"))["available"])
        self.assertIn("--user", self.calls[-1])


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self._run = proc.run
        self._probe = network.probe
        self.services = {}

        async def fake_run(cmd, timeout=10.0, home=None):
            if cmd[:2] == ["ip", "-4"]:
                return 0, (
                    "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
                    "2: wlan0    inet 192.168.2.47/24 brd 192.168.2.255 scope global dynamic wlan0\n"
                ), ""
            name = cmd[-1]
            state = self.services.get(name)
            if "list-unit-files" in cmd:
                return 0, (f"{name} enabled\n" if state is not None else ""), ""
            return (0 if state else 3), "", ""

        proc.run = fake_run

    def tearDown(self):
        proc.run = self._run
        network.probe = self._probe

    def test_flatpak_capture_limitation_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = os.path.join(tmp, ".var", "app", "dev.lizardbyte.app.Sunshine", "config", "sunshine")
            os.makedirs(log_dir)
            with open(os.path.join(log_dir, "sunshine.log"), "w") as fh:
                fh.write("[2026-10-22 13:00:00.000]: Error: AppImage and Flatpak do not support KMS capture. "
                         "Use another capture method.\n")
            result = asyncio.run(diagnostics.run({"running": True}, tmp))
            capture = next(c for c in result["checks"] if c["id"] == "capture")
            self.assertFalse(capture["ok"])
            self.assertIn("Error 503", capture["hint"])

    def test_lan_addresses_physical_first(self):
        ips = asyncio.run(diagnostics.local_ipv4())
        self.assertEqual([i["address"] for i in ips], ["192.168.2.47", "172.17.0.1"])

    def test_discovery_problem_is_reported_with_hint(self):
        async def fake_probe(address, port, timeout=1.5):
            return {"online": True, "busy": False, "hostname": "steamdeck"}

        network.probe = fake_probe
        self.services = {"avahi-daemon.service": False}
        result = asyncio.run(diagnostics.run({"running": True}, "/nonexistent"))
        checks = {c["id"]: c for c in result["checks"]}
        self.assertTrue(checks["running"]["ok"])
        self.assertTrue(checks["server"]["ok"])
        self.assertFalse(checks["discovery"]["ok"])
        self.assertIn("avahi-daemon", checks["discovery"]["hint"])
        self.assertTrue(checks["firewall"]["ok"], "no firewall installed")
        self.assertEqual(checks["running"]["hint"], "", "passing checks carry no hint")


class FakePairApi:
    """Mimics SunshineApi pairing calls for the new (ids) or legacy (PIN only) API."""

    def __init__(self, new_api, pending=(), pin_ok=True, appear=True):
        self.new_api, self.pending, self.pin_ok, self.appear = new_api, list(pending), pin_ok, appear
        self.paired = [{"uuid": "old", "name": "Old phone", "enabled": True}]
        self.sent = []

    async def pending_pairings(self):
        if not self.new_api:
            return {"ok": True, "supported": False, "items": [], "error": None}
        return {"ok": True, "supported": True, "items": self.pending, "error": None}

    async def submit_pin(self, pin, name, pairing_id=None):
        self.sent.append((pin, pairing_id))
        if self.pin_ok and self.appear:
            self.paired.append({"uuid": "new", "name": "Pixel 8", "enabled": True})
        return {"ok": self.pin_ok, "status": 200, "error": None if self.pin_ok else "rejected"}

    async def clients(self):
        return {"ok": True, "items": list(self.paired), "error": None}


class PairingTests(unittest.TestCase):
    def setUp(self):
        self._wait = sunshine.LEGACY_PAIR_WAIT_S
        sunshine.LEGACY_PAIR_WAIT_S = 2.0

    def tearDown(self):
        sunshine.LEGACY_PAIR_WAIT_S = self._wait

    def test_new_api_uses_the_pending_request(self):
        api = FakePairApi(True, pending=[{"id": "a" * 32, "name": "Pixel 8", "address": "192.168.2.10"}])
        result = asyncio.run(sunshine.pair_client(api, "1234", "Deck"))
        self.assertEqual((result["ok"], result["client"]), (True, "Pixel 8"))
        self.assertEqual(api.sent, [("1234", "a" * 32)])

    def test_new_api_without_waiting_device(self):
        result = asyncio.run(sunshine.pair_client(FakePairApi(True), "1234", "Deck"))
        self.assertFalse(result["ok"])
        self.assertIn("waiting", result["error"])

    def test_new_api_wrong_pin(self):
        api = FakePairApi(True, pending=[{"id": "b" * 32, "name": "TV", "address": ""}], pin_ok=False)
        result = asyncio.run(sunshine.pair_client(api, "0000", "Deck"))
        self.assertFalse(result["paired"])
        self.assertIn("wrong PIN", result["error"])

    def test_several_waiting_devices_need_a_choice(self):
        pending = [{"id": "c" * 32, "name": "A", "address": ""}, {"id": "d" * 32, "name": "B", "address": ""}]
        result = asyncio.run(sunshine.pair_client(FakePairApi(True, pending=pending), "1234", "Deck"))
        self.assertEqual(len(result["choose"]), 2)

    def test_legacy_api_confirms_with_client_list(self):
        result = asyncio.run(sunshine.pair_client(FakePairApi(False), "1234", "Deck"))
        self.assertEqual((result["paired"], result["client"]), (True, "Pixel 8"))

    def test_old_sunshine_without_pending_endpoint_uses_legacy_flow(self):
        """Sunshine 2026.516 answers GET /api/pin with 404; pairing must still work."""
        class Old(sunshine.SunshineApi):
            def __init__(self):
                super().__init__("127.0.0.1", "u", "p")
                self.paired = [{"uuid": "old", "name": "Old phone"}]
                self.sent = []

            def _request(self, method, path, body, timeout, limit=0, raw_text=False):
                if (method, path) == ("GET", "/api/pin"):
                    return 404, None
                if (method, path) == ("POST", "/api/pin"):
                    self.sent.append(body)
                    self.paired.append({"uuid": "new", "name": "Pixel 8"})
                    return 200, {"status": "true"}
                if (method, path) == ("GET", "/api/clients/list"):
                    return 200, {"status": True, "named_certs": list(self.paired)}
                raise AssertionError((method, path))

        api = Old()
        result = asyncio.run(sunshine.pair_client(api, "1234", "Deck"))
        self.assertEqual((result["ok"], result["client"]), (True, "Pixel 8"))
        self.assertNotIn("pairing_id", api.sent[0], "the old API takes the PIN only")

    def test_legacy_api_reports_when_device_never_appears(self):
        result = asyncio.run(sunshine.pair_client(FakePairApi(False, appear=False), "1234", "Deck"))
        self.assertFalse(result["paired"])
        self.assertIn("did not finish pairing", result["error"])


class SunshineApiTests(unittest.TestCase):
    def test_first_run_redirect_is_not_followed(self):
        """Sunshine without a login redirects to /welcome (307); following it would look like success."""
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/welcome":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<html>welcome</html>")
                    return
                self.send_response(307)
                self.send_header("Location", "/welcome")
                self.end_headers()

            do_POST = do_GET

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            api = sunshine.SunshineApi("127.0.0.1", "", "")
            api._url = lambda path: f"http://127.0.0.1:{server.server_port}{path}"
            result = asyncio.run(api.test())
            self.assertFalse(result["ok"])
            self.assertTrue(result["needsSetup"])
            self.assertIn("no login yet", result["error"])
            pending = asyncio.run(api.pending_pairings())
            self.assertFalse(pending["ok"])
            outcome = asyncio.run(sunshine.pair_client(api, "1234", "Deck"))
            self.assertIn("no login yet", outcome["error"])
        finally:
            server.shutdown()


class SettingsTests(unittest.TestCase):
    def test_deep_merge_deletes_with_none(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": None, "d": 3}})
        self.assertEqual(merged, {"a": {"c": 2, "d": 3}})

    def test_secrets_are_masked_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(os.path.join(tmp, "settings.json"))
            store.update({"sunshine": {"user": "admin", "password": "hunter2"}})
            public = store.public()
            self.assertNotIn("password", public["sunshine"])
            self.assertTrue(public["sunshine"]["passwordSet"])
            # Echoing the public view back must not wipe the stored secret.
            store.update(strip_masked_fields({"sunshine": {**public["sunshine"], "password": ""}}))
            self.assertEqual(store.data["sunshine"]["password"], "hunter2")
            self.assertEqual(oct(os.stat(store.path).st_mode & 0o777), "0o600")
            reloaded = SettingsStore(store.path)
            self.assertEqual(reloaded.data["sunshine"]["user"], "admin")

    def test_migration_enables_quit_after_for_old_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as fh:
                json.dump({"version": 1, "stream": {"quitAppAfter": False, "fps": 90}}, fh)
            store = SettingsStore(path)
            self.assertTrue(store.data["stream"]["quitAppAfter"])
            self.assertEqual(store.data["stream"]["fps"], 90, "other values are kept")
            with open(path) as fh:
                self.assertEqual(json.load(fh)["version"], 2, "migration is persisted")
            # Once migrated, the user's choice is respected again.
            store.update({"stream": {"quitAppAfter": False}})
            self.assertFalse(SettingsStore(path).data["stream"]["quitAppAfter"])

    def test_fresh_install_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(os.path.join(tmp, "settings.json"))
            self.assertTrue(store.data["stream"]["quitAppAfter"])
            self.assertFalse(os.path.exists(store.path), "nothing is written until something changes")

    def test_mask_nested_host_secret(self):
        masked = mask_secrets({"hosts": {"x": {"sunshinePassword": "p", "sunshineUser": "u"}}})
        self.assertEqual(masked, {"hosts": {"x": {"sunshinePasswordSet": True, "sunshineUser": "u"}}})


class NetworkTests(unittest.TestCase):
    def test_parse_serverinfo(self):
        body = (
            "<?xml version='1.0'?><root status_code=\"200\"><hostname>PC</hostname>"
            "<appversion>7.1.431.-1</appversion><mac>AA:BB:CC:DD:EE:FF</mac>"
            "<currentgame>881448767</currentgame><state>SUNSHINE_SERVER_BUSY</state></root>"
        )
        info = network.parse_serverinfo(body)
        self.assertTrue(info["online"])
        self.assertTrue(info["busy"])
        self.assertEqual(info["currentGame"], "881448767")
        idle = network.parse_serverinfo("<root><currentgame>0</currentgame><state>SUNSHINE_SERVER_FREE</state></root>")
        self.assertFalse(idle["busy"])
        self.assertIsNone(idle["currentGame"])
        self.assertEqual(info["mac"], "aa:bb:cc:dd:ee:ff")
        self.assertEqual(info["version"], "7.1.431.-1")

    def test_normalize_mac(self):
        self.assertEqual(network.normalize_mac("AA-BB-CC-DD-EE-FF"), "aa:bb:cc:dd:ee:ff")
        self.assertIsNone(network.normalize_mac("00:00:00:00:00:00"))
        self.assertIsNone(network.normalize_mac("nope"))

    def test_split_address(self):
        self.assertEqual(network.split_address("10.0.0.2"), ("10.0.0.2", 47989))
        self.assertEqual(network.split_address("10.0.0.2:48000"), ("10.0.0.2", 48000))
        self.assertEqual(network.split_address("[fe80::1]:48000"), ("fe80::1", 48000))
        self.assertEqual(network.split_address("fe80::1"), ("fe80::1", 47989))

    def test_probe_against_local_server(self):
        body = b"<root status_code=\"200\"><hostname>LOCAL</hostname><state>SUNSHINE_SERVER_FREE</state></root>"

        async def scenario():
            async def handle(reader, writer):
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
                await writer.drain()
                writer.close()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                hit = await network.probe_any([{"address": "127.0.0.1", "port": port}], 1.0)
                miss = await network.probe("127.0.0.1", 1, 0.5)
            return hit, miss

        hit, miss = asyncio.run(scenario())
        self.assertIsNotNone(hit)
        self.assertEqual(hit["hostname"], "LOCAL")
        self.assertFalse(hit["busy"])
        self.assertIsNone(miss)


class LaunchRequestTests(unittest.TestCase):
    def test_request_roundtrip_and_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch.write_request(tmp, {"action": "stream", "host": "PC", "hostUuid": "u", "app": "Desktop"})
            request = launch.consume_request(tmp)
            self.assertEqual(request["app"], "Desktop")
            self.assertIsNone(launch.consume_request(tmp), "requests are one-shot")

    def test_stale_request_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, REQUEST_FILE), "w") as fh:
                json.dump({"action": "gui", "createdAt": time.time() - 3600}, fh)
            self.assertIsNone(launch.consume_request(tmp))

    def test_build_argv(self):
        cmd = ["flatpak", "run", "com.moonlight_stream.Moonlight"]
        settings = {"stream": dict(STREAM_DEFAULTS), "hosts": {}}
        argv = launch.build_argv(cmd, {"action": "stream", "host": "PC", "app": "Steam Big Picture"}, settings)
        self.assertEqual(argv[:6], [*cmd, "stream", "PC", "Steam Big Picture"])
        pair = launch.build_argv(cmd, {"action": "pair", "host": "10.0.0.2", "pin": "0420"}, settings)
        self.assertEqual(pair[3:], ["pair", "10.0.0.2", "--pin", "0420"])
        self.assertEqual(launch.build_argv(cmd, {"action": "gui"}, settings), cmd)


def make_tar(entries) -> bytes:
    """gzip tar from (name, data | ("symlink", target) | None for a directory)."""
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(data, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tar.addfile(info)
            else:
                info.size, info.mode = len(data), 0o755 if name.endswith("bin/sunshine") else 0o644
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_deb(data_tar: bytes) -> bytes:
    out = b"!<arch>\n"
    for name, data in (("debian-binary", b"2.0\n"), ("data.tar.gz", data_tar)):
        out += f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


class NativeSunshineTests(unittest.TestCase):
    def test_unpacks_only_what_sunshine_needs(self):
        deb = make_deb(make_tar([
            ("./usr/bin/sunshine", b"ELF /usr/share/sunshine/web"),
            ("./usr/share/sunshine/", None),
            ("./usr/share/sunshine/web/index.html", b"<html>"),
            ("./usr/share/sunshine/../../../etc/evil", b"x"),
            ("./usr/share/applications/sunshine.desktop", b"[Desktop Entry]"),
        ]))
        with tempfile.TemporaryDirectory() as tmp, sunshine_native.deb_data(deb) as tar:
            sunshine_native.extract(tar, sunshine_native.sunshine_files(tar), tmp)
            found = sorted(os.path.relpath(os.path.join(d, f), tmp) for d, _, files in os.walk(tmp) for f in files)
            self.assertEqual(found, ["bin/sunshine", "web/index.html"])
            self.assertTrue(os.access(os.path.join(tmp, "bin", "sunshine"), os.X_OK))

    def test_libraries_keep_soname_links_but_not_outside_links(self):
        tar_bytes = make_tar([
            ("./usr/lib/x86_64-linux-gnu/libnuma.so.1.0.0", b"lib"),
            ("./usr/lib/x86_64-linux-gnu/libnuma.so.1", ("symlink", "libnuma.so.1.0.0")),
            ("./usr/lib/x86_64-linux-gnu/libevil.so", ("symlink", "/etc/shadow")),
            ("./usr/share/doc/libnuma1/copyright", b"text"),
        ])
        with tempfile.TemporaryDirectory() as tmp, sunshine_native.deb_data(make_deb(tar_bytes)) as tar:
            sunshine_native.extract(tar, sunshine_native.library_files(tar), tmp)
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "lib"))), ["libnuma.so.1", "libnuma.so.1.0.0"])
            self.assertEqual(os.readlink(os.path.join(tmp, "lib", "libnuma.so.1")), "libnuma.so.1.0.0")

    def test_asset_prefix_keeps_binary_length(self):
        data = b"\0/usr/share/sunshine\0/usr/share/sunshine/web\0"
        patched, places = sunshine_native.patch_assets_prefix(data)
        self.assertEqual((places, len(patched)), (2, len(data)))
        self.assertIn(b"/home/.ecl/sunshine/web", patched)

    def test_literals_sharing_the_end_of_the_prefix_survive(self):
        # The linker stores "sunshine" (the config directory name) in the last bytes of
        # "/usr/share/sunshine": patching must leave those bytes as they were.
        data = b"\0/usr/share/sunshine\0"
        literal_at = data.index(b"sunshine\0", 2)
        patched, _ = sunshine_native.patch_assets_prefix(data)
        self.assertEqual(patched[literal_at:literal_at + 9], b"sunshine\0")

    def test_picks_the_ubuntu_package_of_both_naming_schemes(self):
        for name in ("sunshine-ubuntu-22.04-amd64.deb", "sunshine_2026.914.233613-1+ubuntu22.04_amd64.deb"):
            release = {"tag_name": "v2026.914.233613", "assets": [
                {"name": "sunshine-ubuntu-24.04-amd64.deb", "browser_download_url": "x"},
                {"name": name, "browser_download_url": sunshine_native.DOWNLOAD_PREFIX + "v2026.914.233613/" + name},
            ]}
            self.assertEqual(sunshine_native.pick_deb_asset(release)["version"], "2026.914.233613")
        foreign = {"tag_name": "v2026.914.233613", "assets": [
            {"name": "sunshine-ubuntu-22.04-amd64.deb", "browser_download_url": "https://example.com/sunshine-ubuntu-22.04-amd64.deb"},
        ]}
        self.assertIsNone(sunshine_native.pick_deb_asset(foreign), "only GitHub downloads of that release")
        with self.assertRaises(ValueError):
            sunshine_native.release_api_url("v1; rm -rf /")

    def test_config_drops_forced_composition_and_disables_tray(self):
        text = "\n".join([
            "min_log_level = info",
            "system_tray = enabled",
            'global_prep_cmd = [{"do":"sh -c \\"xprop GAMESCOPE_COMPOSITE_FORCE 1\\""},{"do":"echo hi"}]',
        ])
        out = sunshine_native.prepare_config(text)
        self.assertIn("min_log_level = info", out)
        self.assertIn('global_prep_cmd = [{"do":"echo hi"}]', out)
        self.assertNotIn("GAMESCOPE_COMPOSITE_FORCE", out)
        self.assertEqual(out.count("system_tray"), 1)
        self.assertIn("system_tray = disabled", out)

    def test_state_reports_outdated_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, helpers = os.path.join(tmp, "root"), os.path.join(tmp, "helpers")
            for path, data in (
                ("root/bin/sunshine", b"bin"), ("root/bin/eclipse-sunshine", b"old"),
                ("root/lib/libeclipse-sunshine.so", b"lib"), ("helpers/eclipse-sunshine", b"new"),
                ("helpers/libeclipse-sunshine.so", b"lib"),
            ):
                os.makedirs(os.path.dirname(os.path.join(tmp, path)), exist_ok=True)
                with open(os.path.join(tmp, path), "wb") as fh:
                    fh.write(data)
            with open(os.path.join(root, "eclipse.json"), "w") as fh:
                json.dump({"version": "2026.914.233613"}, fh)
            state = sunshine_native.state(helpers, root)
            self.assertEqual((state["installed"], state["version"], state["helpersOutdated"]), (True, "2026.914.233613", True))
            self.assertFalse(state["captureReady"], "no file capability in a temp dir")
            self.assertFalse(sunshine_native.state(helpers, os.path.join(tmp, "missing"))["installed"])

    def test_legacy_installation_needs_a_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "legacy")
            for name in ("bin/sunshine", "bin/eclipse-sunshine"):
                os.makedirs(os.path.dirname(os.path.join(legacy, name)), exist_ok=True)
                open(os.path.join(legacy, name), "wb").close()
            original = (sunshine_native.ROOT, sunshine_native.LEGACY_ROOTS)
            sunshine_native.ROOT, sunshine_native.LEGACY_ROOTS = os.path.join(tmp, "new"), (legacy,)
            try:
                state = sunshine_native.state(None, sunshine_native.ROOT)
            finally:
                sunshine_native.ROOT, sunshine_native.LEGACY_ROOTS = original
            self.assertEqual((state["installed"], state["legacy"], state["helpersOutdated"]), (True, True, True))


class SudoTests(unittest.TestCase):
    def run_root(self, lines, rc):
        async def fake_streaming(cmd, on_line, timeout=0, home=None, input=None):
            self.assertEqual(cmd[:2], ["sudo", "-S"])
            self.assertEqual(input, b"secret\n")
            for line in lines:
                on_line(line)
            return rc

        local = sunshine.LocalSunshine("/nonexistent", "/tmp", "/plugin")
        seen = []
        original = proc.run_streaming
        proc.run_streaming = fake_streaming
        try:
            return asyncio.run(local._run_root(["uninstall"], "secret", seen.append)), seen
        finally:
            proc.run_streaming = original

    def test_wrong_password(self):
        result, seen = self.run_root(["Sorry, try again.", "sudo: 1 incorrect password attempt"], 1)
        self.assertTrue(result["passwordRejected"])
        self.assertIn("Wrong password", result["error"])
        self.assertEqual(seen, [], "sudo's own output is not shown as progress")

    def test_installer_error_is_reported(self):
        result, seen = self.run_root([sunshine.ROOT_READY, "Downloading...", "ERROR: Checksum mismatch for x"], 1)
        self.assertEqual(result["error"], "Checksum mismatch for x")
        self.assertNotIn("passwordRejected", result)
        self.assertEqual(seen[0], "Downloading...")

    def test_success(self):
        result, _ = self.run_root([sunshine.ROOT_READY, "Removed Sunshine for Gaming Mode"], 0)
        self.assertTrue(result["ok"])

    def test_empty_password_is_not_sent(self):
        local = sunshine.LocalSunshine("/nonexistent", "/tmp", "/plugin")
        result = asyncio.run(local._run_root(["uninstall"], "", lambda _: None))
        self.assertFalse(result["ok"])


def sunshine_line(message, stamp="2026-10-22 10:00:00"):
    return f"[{stamp}.123]: Info: {message}\n"


class SunshineSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "sunshine.log")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, mode="a"):
        with open(self.log, mode, encoding="utf-8") as fh:
            fh.write(text)

    def test_counts_connects_and_disconnects(self):
        state = sunshine_session.apply_log(
            sunshine_session.LogState(),
            sunshine_line("Sunshine version: v2026.1018.5342")
            + sunshine_line("CLIENT CONNECTED", "2026-10-22 10:00:05")
            + sunshine_line("CLIENT CONNECTED", "2026-10-22 10:01:00")
            + sunshine_line("CLIENT DISCONNECTED"),
        )
        self.assertEqual(state.clients, 1)

    def test_process_terminated_and_startup_reset(self):
        text = sunshine_line("CLIENT CONNECTED") + sunshine_line("Process terminated")
        self.assertEqual(sunshine_session.apply_log(sunshine_session.LogState(), text).clients, 0)
        text = sunshine_line("CLIENT CONNECTED") + sunshine_line("Sunshine version: v1")
        self.assertEqual(sunshine_session.apply_log(sunshine_session.LogState(), text).clients, 0)

    def test_ping_timeout_ends_a_client(self):
        text = sunshine_line("CLIENT CONNECTED") + sunshine_line("192.168.2.10: Ping Timeout")
        self.assertEqual(sunshine_session.apply_log(sunshine_session.LogState(), text).clients, 0)

    def test_follower_reads_incrementally_and_notices_a_rewrite(self):
        follower = sunshine_session.LogFollower()
        self.write(sunshine_line("Sunshine version: v1", "2026-10-22 09:00:00") + sunshine_line("CLIENT CONNECTED"), "w")
        self.assertEqual(follower.read(self.log).clients, 1)
        # An unfinished line is kept until it is complete.
        self.write("[2026-10-22 10:05:00.000]: Info: CLIENT DISC")
        self.assertEqual(follower.read(self.log).clients, 1)
        self.write("ONNECTED\n")
        self.assertEqual(follower.read(self.log).clients, 0)
        self.write(sunshine_line("CLIENT CONNECTED"))
        self.assertEqual(follower.read(self.log).clients, 1)
        # Sunshine restarted and rewrote its log, longer than before.
        self.write(sunshine_line("Sunshine version: v1", "2026-10-22 11:00:00") + sunshine_line("x" * 400), "w")
        self.assertEqual(follower.read(self.log).clients, 0)
        self.assertEqual(follower.read(None).clients, 0)

    def test_app_ids_match_sunshine(self):
        # Sunshine casts the CRC32 to int32 and takes abs().
        import zlib

        value = zlib.crc32(b"Desktop")
        signed = value - (1 << 32) if value >= 1 << 31 else value
        self.assertEqual(sunshine_session._app_ids("Desktop", None, 0)[0], str(abs(signed)))

    def test_summary(self):
        log_state = sunshine_session.LogState(clients=2, since=1000.0)
        self.assertEqual(sunshine_session.summarize(None, log_state, None)["clients"], 0)
        idle = sunshine_session.summarize({"currentGame": None}, log_state, None)
        self.assertEqual((idle["appOpen"], idle["clients"]), (False, 0))
        busy = sunshine_session.summarize({"currentGame": "123"}, log_state, "Desktop")
        self.assertEqual((busy["appOpen"], busy["clients"], busy["since"], busy["appName"]), (True, 2, 1000000, "Desktop"))

    def test_clients_without_open_app_are_dropped(self):
        tracker = sunshine_session.SessionTracker()
        # Closed through the API: no "CLIENT DISCONNECTED" line was logged.
        self.write(sunshine_line("Sunshine version: v1") + sunshine_line("CLIENT CONNECTED"), "w")
        self.assertEqual(tracker.update({"currentGame": "5"}, self.log)["clients"], 1)
        self.assertEqual(tracker.update({"currentGame": None}, self.log)["clients"], 0)
        self.write(sunshine_line("CLIENT CONNECTED"))
        self.assertEqual(tracker.update({"currentGame": "5"}, self.log)["clients"], 1)

    def test_app_name_from_apps_json(self):
        name_id = sunshine_session._app_ids("Steam Big Picture", None, 1)[0]
        with open(os.path.join(self.tmp.name, "apps.json"), "w", encoding="utf-8") as fh:
            json.dump({"apps": [{"name": "Desktop", "image-path": "missing.png"}, {"name": "Steam Big Picture"}]}, fh)
        self.assertEqual(sunshine_session.app_name(name_id, self.tmp.name), "Steam Big Picture")
        self.assertIsNone(sunshine_session.app_name("1", self.tmp.name))


FAKE_PASSWD_NONE = """#!/bin/bash
stty -echo
printf "New password: "; read a; echo
if [ ${#a} -lt 4 ]; then echo "BAD PASSWORD: The password is shorter than 4 characters"; printf "New password: "; read a; exit 1; fi
printf "Retype new password: "; read b; echo
[ "$a" = "$b" ] || { echo "Sorry, passwords do not match."; exit 10; }
echo "$a" > "$(dirname "$0")/result"
echo "passwd: password updated successfully"
"""

FAKE_PASSWD_SET = """#!/bin/bash
echo "Changing password for newton."
sleep 0.2
stty -echo; printf "Current password: "; read a; exit 1
"""


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o755)
        return [path]

    def test_password_state(self):
        self.assertEqual(account.password_state(self.script("none", FAKE_PASSWD_NONE), self.env), "none")
        self.assertEqual(account.password_state(self.script("set", FAKE_PASSWD_SET), self.env), "set")
        self.assertEqual(account.password_state(["/nonexistent/passwd"], self.env), "unknown")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "result")), "checking changes nothing")

    def test_set_first_password(self):
        cmd = self.script("none", FAKE_PASSWD_NONE)
        self.assertIn("shorter", account.set_first_password("abc", cmd, self.env))
        self.assertIsNone(account.set_first_password("hunter22", cmd, self.env))
        with open(os.path.join(self.tmp.name, "result"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "hunter22")

    def test_existing_password_is_not_replaced(self):
        error = account.set_first_password("hunter22", self.script("set", FAKE_PASSWD_SET), self.env)
        self.assertIn("already has a password", error)


class DiscoveryTests(unittest.TestCase):
    def test_scan_targets(self):
        targets = network.scan_targets([{"interface": "wlan0", "address": "192.168.2.47", "prefix": "16"}])
        self.assertEqual(len(targets), 253)
        self.assertNotIn("192.168.2.47", targets)
        self.assertIn("192.168.2.1", targets)
        self.assertEqual(network.scan_targets([{"interface": "wlan0", "address": "8.8.8.8", "prefix": "24"}]), [])
        self.assertEqual(len(network.scan_targets([{"interface": "wlan0", "address": "10.0.0.5", "prefix": "30"}])), 1)

    def test_discover_lists_each_host_once(self):
        original = network.probe

        async def fake_probe(address, port, timeout=1.5):
            if address in ("192.168.2.10", "192.168.2.11"):
                return {"address": address, "hostname": "PC", "uniqueId": "pc-1"}
            if address == "192.168.2.20":
                return {"address": address, "hostname": "Another", "uniqueId": "pc-2"}
            return None

        network.probe = fake_probe
        try:
            found = asyncio.run(network.discover([{"interface": "wlan0", "address": "192.168.2.47", "prefix": "24"}]))
        finally:
            network.probe = original
        self.assertEqual([f["hostname"] for f in found], ["Another", "PC"])


class HostChecksTests(unittest.TestCase):
    HOST = {"uuid": "u", "name": "PC", "addresses": [{"address": "192.168.2.10", "port": 47989}], "paired": True}
    LOCAL = [{"interface": "wlan0", "address": "192.168.2.47", "prefix": "24"}]

    def by_id(self, checks):
        return {c["id"]: c for c in checks}

    def test_offline_host(self):
        checks = self.by_id(diagnostics.host_checks(self.HOST, None, True, self.LOCAL, None, None))
        self.assertFalse(checks["reachable"]["ok"])
        self.assertIn("192.168.2.10", checks["reachable"]["detail"])
        self.assertIsNone(checks["wake"]["ok"])
        self.assertNotIn("sunshine", checks)

    def test_online_host_with_login(self):
        info = {"address": "192.168.2.10", "latencyMs": 4, "version": "2026.1018", "busy": True}
        checks = self.by_id(diagnostics.host_checks(
            self.HOST, info, True, self.LOCAL, "aa:bb:cc:dd:ee:ff", {"ok": False, "error": None},
        ))
        self.assertTrue(checks["reachable"]["ok"])
        self.assertTrue(checks["wake"]["ok"])
        self.assertFalse(checks["login"]["ok"])
        self.assertIn("busy", checks)


class AutoOffTests(unittest.TestCase):
    UP = {"reachable": True, "clients": 0}
    STREAMING = {"reachable": True, "clients": 1}
    DOWN = {"reachable": False, "clients": 0}

    def test_never(self):
        auto = sunshine_session.AutoOff()
        self.assertIsNone(auto.update(self.UP, "never", 0))
        self.assertIsNone(auto.update(self.UP, "never", 10_000))

    def test_after_stream_needs_a_stream_and_a_minute(self):
        auto = sunshine_session.AutoOff()
        self.assertIsNone(auto.update(self.UP, "afterStream", 0))
        self.assertIsNone(auto.update(self.UP, "afterStream", 600), "nobody streamed yet")
        auto.update(self.STREAMING, "afterStream", 700)
        self.assertIsNone(auto.update(self.UP, "afterStream", 710))
        self.assertIsNone(auto.update(self.UP, "afterStream", 760))
        self.assertEqual(auto.update(self.UP, "afterStream", 771), "the stream ended")

    def test_changing_the_option_counts_from_then(self):
        auto = sunshine_session.AutoOff()
        auto.update(self.UP, "never", 0)
        self.assertIsNone(auto.update(self.UP, "15", 1300))
        self.assertEqual(auto.update(self.UP, "15", 1300 + 15 * 60), "nobody streamed for 15 minutes")

    def test_after_stream_waits_longer_while_the_app_is_open(self):
        auto = sunshine_session.AutoOff()
        auto.update(self.STREAMING, "afterStream", 0)
        lingering = {"reachable": True, "clients": 0, "appOpen": True}
        auto.update(lingering, "afterStream", 10)
        self.assertIsNone(auto.update(lingering, "afterStream", 200))
        self.assertEqual(auto.update(lingering, "afterStream", 311), "the stream ended")

    def test_idle_minutes_restart_with_a_stream_and_with_sunshine(self):
        auto = sunshine_session.AutoOff()
        auto.update(self.UP, "15", 0)
        auto.update(self.STREAMING, "15", 800)
        self.assertIsNone(auto.update(self.UP, "15", 900))
        self.assertEqual(auto.update(self.UP, "15", 900 + 15 * 60), "nobody streamed for 15 minutes")
        auto.update(self.DOWN, "15", 5000)
        self.assertIsNone(auto.update(self.UP, "15", 5001))


def state_json(*devices):
    return {"root": {"uniqueid": "host-id", "named_devices": [
        {"name": name, "cert": cert, "uuid": uuid, "enabled": "true"} for name, cert, uuid in devices
    ]}, "username": "deck", "password": "hash", "salt": "salt"}


class SunshineDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.new = os.path.join(self.home, ".config", "sunshine")
        self.legacy = os.path.join(self.home, ".config", "pse-sun")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_duplicates_keep_the_newest_record(self):
        state = state_json(("Phone", "CERT-A", "u1"), ("TV", "CERT-B", "u2"), ("Phone", "CERT-A\n", "u3"))
        self.assertEqual(sunshine_data.duplicate_clients(state), ["u1"])
        self.assertEqual(sunshine_data.duplicate_clients({}), [])

    def test_dedupe_state_file(self):
        path = os.path.join(self.new, "sunshine_state.json")
        self.write(path, json.dumps(state_json(("Phone", "A", "u1"), ("Phone", "A", "u2"), ("Phone", "A", "u3"))))
        self.assertEqual(sunshine_data.dedupe_state_file(path), 2)
        data = json.load(open(path))
        self.assertEqual([d["uuid"] for d in data["root"]["named_devices"]], ["u3"])
        self.assertEqual(data["username"], "deck", "the login is kept")
        self.assertEqual(sunshine_data.dedupe_state_file(path), 0)

    def test_migrate_moves_legacy_data_back(self):
        self.write(os.path.join(self.new, "sunshine.conf"), "system_tray = disabled\n")
        self.write(os.path.join(self.legacy, "sunshine.conf"), "encoder = vaapi\n")
        self.write(os.path.join(self.legacy, "credentials", "cacert.pem"), "server cert")
        self.write(os.path.join(self.legacy, "sunshine_state.json"),
                   json.dumps(state_json(("Phone", "A", "u1"), ("Phone", "A", "u2"))))
        done = sunshine_data.migrate(self.home)
        self.assertFalse(os.path.exists(self.legacy))
        self.assertEqual(open(os.path.join(self.new, "sunshine.conf")).read(), "encoder = vaapi\n")
        self.assertEqual(open(os.path.join(self.new, "credentials", "cacert.pem")).read(), "server cert")
        devices = json.load(open(os.path.join(self.new, "sunshine_state.json")))["root"]["named_devices"]
        self.assertEqual([d["uuid"] for d in devices], ["u2"])
        self.assertTrue(os.path.exists(os.path.join(self.new, "moved-by-eclipse", "sunshine", "sunshine.conf")))
        self.assertEqual(len(done), 2)
        self.assertEqual(sunshine_data.migrate(self.home), [], "nothing left to do")

    def test_remove_all(self):
        self.write(os.path.join(self.new, "a"), "x")
        self.write(os.path.join(self.legacy, "b"), "y")
        sunshine_data.remove_all(self.home)
        self.assertFalse(os.path.exists(self.new) or os.path.exists(self.legacy))


class PairingNameTests(unittest.TestCase):
    def test_moonlight_placeholder_is_no_name(self):
        self.assertEqual(sunshine.pairing_device_name("roth"), "")
        self.assertEqual(sunshine.pairing_device_name(" Roth "), "")
        self.assertEqual(sunshine.pairing_device_name("Linh's phone"), "Linh's phone")
        self.assertEqual(sunshine.plain_address("::ffff:192.168.1.5"), "192.168.1.5")
        self.assertEqual(sunshine.plain_address("fe80::1"), "fe80::1")


class SunshineConfigTests(unittest.TestCase):
    def test_reads_values_and_defaults(self):
        values = sunshine_config.read("# comment\nstream_audio = off\nmax_bitrate = 20000\ngamepad = nope\n")
        self.assertFalse(values["stream_audio"])
        self.assertEqual(values["max_bitrate"], 20000)
        self.assertEqual(values["gamepad"], "auto", "an invalid value reads as the default")
        self.assertTrue(values["controller"])
        self.assertEqual(sunshine_config.read("fec_percentage = 30   # lossy wifi\n")["fec_percentage"], 30)

    def test_apply_keeps_other_lines_and_drops_defaults(self):
        text = "system_tray = disabled\nstream_audio = disabled\noutput_name = 1\nstream_audio = enabled\n"
        out = sunshine_config.apply(text, {"stream_audio": True, "max_bitrate": 30000, "gamepad": "ds5"})
        self.assertEqual(out, "system_tray = disabled\noutput_name = 1\nmax_bitrate = 30000\ngamepad = ds5\n")
        out = sunshine_config.apply(out, {"mouse": False, "max_bitrate": 10000})
        self.assertIn("max_bitrate = 10000\n", out)
        self.assertIn("mouse = disabled\n", out)
        self.assertEqual(sunshine_config.apply("", {"upnp": False}), "")

    def test_invalid_values_are_rejected(self):
        for patch in ({"max_bitrate": -5}, {"stream_audio": "yes"}, {"gamepad": "x360"}, {"nope": 1},
                      {"sunshine_name": "a\nb"}, {"sunshine_name": "Deck #1"}, {"sunshine_name": "[Deck"}, {"fec_percentage": 1.5}, {"ping_timeout": True}):
            with self.assertRaises(ValueError, msg=str(patch)):
                sunshine_config.apply("", patch)


class MdnsTests(unittest.TestCase):
    service = mdns.Service("Ally", "ally-sunshine", 47989, ("192.168.2.47",))

    def query(self, name, qtype, msg_id=0, unicast=False):
        qclass = mdns.CLASS_IN | (mdns.CLASS_TOP_BIT if unicast else 0)
        import struct
        return struct.pack("!HHHHHH", msg_id, 0, 1, 0, 0, 0) + mdns.encode_name(name) + struct.pack("!HH", qtype, qclass)

    def test_browse_query_gets_ptr_with_srv_txt_and_address(self):
        query = mdns.parse_query(self.query("_nvstream._tcp.local", mdns.TYPE_PTR))
        answers, additional = mdns.answer(query, self.service)
        self.assertEqual([r.rtype for r in answers], [mdns.TYPE_PTR])
        self.assertEqual(sorted(r.rtype for r in additional), [mdns.TYPE_A, mdns.TYPE_TXT, mdns.TYPE_SRV])
        packet = mdns.build_response(answers, additional)
        self.assertIsNone(mdns.parse_query(packet), "a response is not taken for a query")
        self.assertIn(b"\x04_tcp\x05local", packet)

    def test_names_are_case_insensitive_and_others_ignored(self):
        query = mdns.parse_query(self.query("ALLY-SUNSHINE.local", mdns.TYPE_A, unicast=True))
        self.assertTrue(query.questions[0].unicast)
        answers, _ = mdns.answer(query, self.service)
        self.assertEqual(answers[0].rdata, bytes([192, 168, 2, 47]))
        other = mdns.parse_query(self.query("_airplay._tcp.local", mdns.TYPE_PTR))
        self.assertEqual(mdns.answer(other, self.service), ([], []))

    def test_compressed_names_and_garbage(self):
        import struct
        data = struct.pack("!HHHHHH", 0, 0, 2, 0, 0, 0) + mdns.encode_name("_nvstream._tcp.local")
        data += struct.pack("!HH", 12, 1) + b"\xc0\x0c" + struct.pack("!HH", 255, 1)
        query = mdns.parse_query(data)
        self.assertEqual([q.name for q in query.questions], ["_nvstream._tcp.local"] * 2)
        self.assertIsNone(mdns.parse_query(data[:-3]))
        self.assertIsNone(mdns.parse_query(b"\x00" * 5 + b"\x01" + b"\x00" * 6 + b"\xc0\x0c"), "pointer loop")

    def test_publish_failure_only_counts_for_the_latest_run(self):
        failed = "[t]: Error: avahi::entry_group_new() failed: Not permitted\n"
        self.assertTrue(mdns.publish_failed("[t]: Info: Sunshine version: 1\n" + failed))
        self.assertFalse(mdns.publish_failed(failed + "[t]: Info: Sunshine version: 2\nAvahi service x successfully established."))

    def test_labels(self):
        self.assertEqual(mdns.dns_label("1m93 Ally!-sunshine"), "1m93-Ally-sunshine")
        self.assertEqual(mdns.dns_label("!!!"), "device")
        self.assertLessEqual(len(mdns.instance_label("é" * 80).encode()), 63)
        self.assertEqual(mdns.instance_label("Deck 2.0."), "Deck 2 0")
        mdns.Service(mdns.instance_label("Deck."), "h", 1, ("10.0.0.1",)).records()


class SunshineAppsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.steamapps = os.path.join(self.home, ".local", "share", "Steam", "steamapps")
        os.makedirs(self.steamapps)
        self.config = os.path.join(self.home, ".config", "sunshine")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, folder, app_id, name, flags=4):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"appmanifest_{app_id}.acf"), "w") as fh:
            fh.write(f'"AppState"\n{{\n\t"appid"\t\t"{app_id}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n}}\n')

    def test_installed_games_across_libraries_without_tools(self):
        sd = os.path.join(self.home, "sd", "steamapps")
        with open(os.path.join(self.steamapps, "libraryfolders.vdf"), "w") as fh:
            fh.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                     % (os.path.dirname(self.steamapps), os.path.dirname(sd)))
        self.manifest(self.steamapps, 620, "Portal 2")
        self.manifest(self.steamapps, 1493710, "Proton Experimental")
        self.manifest(self.steamapps, 999, 'Half \\"Quoted\\" Game', flags=6)
        self.manifest(sd, 1145360, "Hades")
        self.manifest(sd, 70, "Downloading", flags=1026 & ~4)
        games = sunshine_apps.installed_games(self.home)
        self.assertEqual([g["name"] for g in games], ['Hades', 'Half "Quoted" Game', "Portal 2"])

    def test_add_and_remove_games_keep_other_apps(self):
        added = sunshine_apps.add_games(self.config, [{"appId": 620, "name": "Portal 2"}, {"appId": 620, "name": "x"}], False)
        self.assertEqual(added, 1)
        apps = sunshine_apps.list_apps(self.config)
        self.assertEqual([a["name"] for a in apps], ["Desktop", "Steam Big Picture", "Portal 2"])
        self.assertEqual(apps[2]["steamAppId"], 620)
        self.assertEqual(apps[1]["steamAppId"], None, "Big Picture is not a game")
        data = json.load(open(sunshine_apps.apps_path(self.config)))
        self.assertEqual(data["apps"][2]["detached"], ["setsid steam steam://rungameid/620"])
        undo = data["apps"][2]["prep-cmd"][0]["undo"]
        self.assertTrue(undo.startswith("sh ") and undo.endswith(" 620"))
        self.assertTrue(os.access(os.path.join(self.config, sunshine_apps.QUIT_SCRIPT), os.X_OK))
        self.assertIsNone(sunshine_apps.steam_app_id({"detached": ["steam steam://rungameid/17293822569102704640"]}))
        self.assertEqual(sunshine_apps.add_games(self.config, [{"appId": 620, "name": "Portal 2"}], False), 0)
        self.assertEqual(sunshine_apps.remove_games(self.config, [620]), 1)
        self.assertEqual(len(sunshine_apps.list_apps(self.config)), 2)

    def test_cover_is_used_and_flatpak_runs_on_host(self):
        import base64
        png = sunshine_apps.PNG_SIGNATURE + b"data"
        sunshine_apps.save_cover(self.config, 620, base64.b64encode(png).decode())
        sunshine_apps.add_games(self.config, [{"appId": 620, "name": "Portal 2"}], True)
        app = sunshine_apps.read_apps(self.config)["apps"][-1]
        self.assertEqual(app["image-path"], sunshine_apps.cover_path(self.config, 620))
        self.assertTrue(app["detached"][0].startswith("flatpak-spawn --host steam"))
        with self.assertRaises(ValueError):
            sunshine_apps.save_cover(self.config, 1, base64.b64encode(b"\xff\xd8jpeg").decode())

    def test_broken_apps_file_is_not_overwritten(self):
        os.makedirs(self.config)
        with open(sunshine_apps.apps_path(self.config), "w") as fh:
            fh.write("{broken")
        with self.assertRaises(ValueError):
            sunshine_apps.add_games(self.config, [{"appId": 1, "name": "A"}], False)
        self.assertEqual(open(sunshine_apps.apps_path(self.config)).read(), "{broken")


class NetTestTests(unittest.TestCase):
    def test_parsers(self):
        wireless = "Inter-| sta-|\n face | tus |\n wlan0: 0000   54.  -56.  -256        0      0\n"
        self.assertEqual(nettest.parse_wireless(wireless, "wlan0"), -56)
        self.assertIsNone(nettest.parse_wireless(wireless, "eth0"))
        link = nettest.parse_iw_link("Connected to x\n\tfreq: 5180\n\tsignal: -52 dBm\n\ttx bitrate: 866.7 MBit/s VHT-MCS 9\n")
        self.assertEqual((link["txMbps"], link["signalDbm"], link["freqMhz"]), (866.7, -52.0, 5180.0))

    def test_suggestions(self):
        wired = nettest.suggest([1.0, 1.2, 0.9], {"wireless": False})
        self.assertEqual((wired["verdict"], wired["suggestedKbps"]), ("great", 100000))
        wifi = nettest.suggest([4.0] * 10, {"wireless": True, "linkMbps": 300, "signalDbm": -70, "freqMhz": 2437})
        self.assertEqual(wifi["suggestedKbps"], 35000)
        self.assertEqual(len(wifi["notes"]), 2)
        lossy = nettest.suggest([50.0, None, 80.0, 20.0], {"wireless": True, "linkMbps": 72, "signalDbm": -80})
        self.assertEqual((lossy["verdict"], lossy["suggestedKbps"], lossy["delay"]), ("poor", 5000, "high"))
        self.assertEqual((lossy["lossPct"], lossy["failed"], lossy["attempts"]), (25, 1, 4))
        vpn = nettest.suggest([40.0, 41.0, 40.5], {"wireless": False, "vpn": True})
        self.assertEqual((vpn["suggestedKbps"], vpn["remote"], vpn["verdict"]), (15000, True, "great"),
                         "steady but far: smooth, with input lag, at a bitrate an upload carries")
        none = nettest.suggest([None, None], {"wireless": False})
        self.assertEqual((none["verdict"], none["suggestedKbps"]), ("unreachable", None))


class TailscaleTests(unittest.TestCase):
    def test_parse_status(self):
        text = json.dumps({
            "BackendState": "Running",
            "Self": {"TailscaleIPs": ["100.64.0.2", "fd7a::2"]},
            "Peer": {
                "a": {"HostName": "gaming-pc", "TailscaleIPs": ["100.64.0.5"], "Online": True},
                "b": {"DNSName": "laptop.tail.ts.net.", "TailscaleIPs": ["100.64.0.9"], "Online": False},
                "c": {"HostName": "v6only", "TailscaleIPs": ["fd7a::9"], "Online": True},
            },
        })
        status = tailscale.parse_status(text)
        self.assertEqual(status["self"], "100.64.0.2")
        self.assertEqual([(p["name"], p["online"]) for p in status["peers"]], [("gaming-pc", True), ("laptop", False)])
        self.assertEqual(tailscale.parse_status("nope")["peers"], [])
        stopped = tailscale.parse_status(json.dumps({"BackendState": "Stopped", "Self": {"TailscaleIPs": ["100.1.1.1"]}}))
        self.assertIsNone(stopped["self"])


class BatteryTests(unittest.TestCase):
    def test_read_battery(self):
        with tempfile.TemporaryDirectory() as root:
            for name, values in (("AC", {"type": "Mains"}), ("BAT1", {"type": "Battery", "capacity": "42", "status": "Discharging"}),
                                 ("hid-pad", {"type": "Battery", "scope": "Device", "capacity": "5", "status": "Discharging"})):
                os.makedirs(os.path.join(root, name))
                for key, value in values.items():
                    open(os.path.join(root, name, key), "w").write(value + "\n")
            self.assertEqual(battery.read(root), {"percent": 42, "charging": False})
            self.assertIsNone(battery.read(os.path.join(root, "missing")))

    def test_guard(self):
        guard = battery.BatteryGuard()
        low = {"percent": 19, "charging": False}
        self.assertIsNone(guard.update(20, low, 0), "started below the level on purpose: left alone")
        self.assertIsNone(guard.update(20, {"percent": 21, "charging": False}, 0), "not re-armed 1% above")
        self.assertIsNone(guard.update(20, low, 0))
        guard.reset()  # Sunshine stopped and started again
        self.assertIsNone(guard.update(20, {"percent": 60, "charging": False}, 0))
        self.assertEqual(guard.update(20, {"percent": 20, "charging": False}, 1), "warn")
        self.assertIsNone(guard.update(20, low, 1), "warned once")
        self.assertEqual(guard.update(20, {"percent": 5, "charging": False}, 1), "stop", "the floor ends the stream")
        guard.reset()
        guard.update(20, {"percent": 60, "charging": False}, 0)
        self.assertEqual(guard.update(20, low, 0), "stop")
        self.assertIsNone(guard.update(20, low, 0), "acts once per fall")
        self.assertIsNone(guard.update(20, {"percent": 19, "charging": True}, 0))
        self.assertEqual(guard.update(20, low, 0), "stop", "unplugged again below the level")
        self.assertIsNone(guard.update(0, low, 0), "off")


class PeerTests(unittest.TestCase):
    def test_tcp_peers(self):
        text = (
            "  sl  local_address rem_address   st\n"
            "   0: 0100007F:BB8D 0100007F:9C40 01\n"              # 127.0.0.1:47757 <- loopback
            "   1: 2F02A8C0:BB70 0502A8C0:D431 01\n"              # 192.168.2.47:47984 <- .5
            "   2: 2F02A8C0:BB70 0602A8C0:D432 06\n"              # TIME_WAIT: a finished exchange counts
            "   4: 2F02A8C0:BB70 2F02A8C0:D433 01\n"              # this device itself
            "   3: 2F02A8C0:9C40 0702A8C0:BB70 01\n"              # outgoing to a host's 47984
        )
        self.assertEqual(sunshine_session.tcp_peers(text, own=frozenset({"192.168.2.47"})), {"192.168.2.5", "192.168.2.6"})
        self.assertEqual(sunshine_session.client_tcp_ports(47989), (47984, 47989, 48010))
        mapped = "  sl local rem st\n   0: 0000000000000000FFFF00002F02A8C0:BB70 0000000000000000FFFF00000902A8C0:D000 01\n"
        self.assertEqual(sunshine_session.tcp_peers(mapped), {"192.168.2.9"})

    def test_tracker(self):
        tracker = sunshine_session.PeerTracker()
        self.assertEqual(tracker.update({"10.0.0.9"}, 0, 0.0), [], "seen long before the stream")
        self.assertEqual(tracker.update({"10.0.0.5"}, 0, 50.0), [])
        self.assertEqual(tracker.update(set(), 1, 60.0), ["10.0.0.5"], "only devices seen just before the start")
        self.assertEqual(tracker.update({"10.0.0.6"}, 2, 70.0), ["10.0.0.5", "10.0.0.6"], "a second client joining")
        self.assertEqual(tracker.update({"10.0.0.7"}, 2, 200.0), ["10.0.0.5", "10.0.0.6"], "later browsers are not added")
        self.assertEqual(tracker.update(set(), 0, 210.0), [])


class BackupTests(unittest.TestCase):
    def test_roundtrip_without_passwords(self):
        settings = json.loads(json.dumps(DEFAULTS))
        settings["sunshine"]["password"] = "secret"
        settings["hosts"] = {"h1": {"sunshineUser": "u", "sunshinePassword": "p", "preset": "p-1"}}
        settings["profiles"] = {"p-1": {"name": "TV", "stream": {"fps": 60}}}
        settings["shortcuts"]["launcherAppId"] = 123
        with tempfile.TemporaryDirectory() as home:
            media = os.path.join(home, "no-media")
            data = backup.build(settings, {"max_bitrate": 20000}, [{"appId": 620, "name": "Portal 2"}], False, "0.2.0", now=0)
            self.assertNotIn("password", data["settings"]["sunshine"])
            self.assertNotIn("shortcuts", data["settings"])
            path = backup.write(home, data, media_root=media)
            second = backup.write(home, data, media_root=media)
            self.assertNotEqual(path, second)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(os.path.join(backup.backup_dir(home), "eclipse-backup (1).json"), "w") as fh:
                fh.write("{oops")
            listed = backup.list_backups(home, media_root=media)
            self.assertEqual(listed[-1]["error"], "The backup file is damaged", "damaged files are listed last")
            listed = listed[:-1]
            self.assertEqual(len(listed), 2)
            self.assertEqual((listed[0]["profiles"], listed[0]["games"], listed[0]["hasPasswords"]), (1, 1, False))
            restored = backup.read(home, "home", os.path.basename(path), media_root=media)
            current = json.loads(json.dumps(settings))
            current["shortcuts"]["launcherAppId"] = 456
            merged = backup.merge_settings(current, restored["settings"], DEFAULTS)
            self.assertEqual(merged["sunshine"]["password"], "secret", "passwords not in the backup are kept")
            self.assertEqual(merged["hosts"]["h1"]["sunshinePassword"], "p")
            current["sunshine"]["user"] = "deck"
            restored["settings"]["sunshine"]["user"] = "linh"
            merged = backup.merge_settings(current, restored["settings"], DEFAULTS)
            self.assertEqual((merged["sunshine"]["user"], merged["sunshine"]["password"]), ("deck", "secret"),
                             "a kept password keeps its user name")
            self.assertEqual(merged["shortcuts"]["launcherAppId"], 456, "local shortcuts are kept")
            self.assertEqual(merged["profiles"]["p-1"]["name"], "TV")
            with self.assertRaises(ValueError):
                backup.read(home, "home", "../settings.json", media_root=media)
            with self.assertRaises(ValueError):
                backup.read(home, "/etc", "passwd.json", media_root=media)
            backup.delete(home, "home", os.path.basename(second), media_root=media)
            self.assertEqual(len(backup.list_backups(home, media_root=media)), 2)

    def test_damaged_values_are_dropped_on_restore(self):
        current = json.loads(json.dumps(DEFAULTS))
        restored = {"sunshine": "oops", "features": [], "behavior": {"autoWake": "yes", "pollIntervalS": 5},
                    "hosts": {"h1": "bad", "h2": {"preset": "x"}}, "profiles": {"p": {"name": 3}}}
        merged = backup.merge_settings(current, restored, DEFAULTS)
        self.assertNotIn("sunshine", merged)
        self.assertEqual(merged["behavior"], {"pollIntervalS": 5})
        self.assertEqual(merged["hosts"], {"h2": {"preset": "x"}})
        self.assertEqual(merged["profiles"], {})

    def test_removable_drives_are_locations(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual([loc["id"] for loc in backup.locations(home, os.path.join(home, "none"))], ["home"])

    def test_invalid_backups(self):
        for data in ({}, {"format": "eclipse-backup", "version": 99, "settings": {}}, {"format": "eclipse-backup", "version": 1}):
            with self.assertRaises(ValueError):
                backup.validate(data)


if __name__ == "__main__":
    unittest.main()
