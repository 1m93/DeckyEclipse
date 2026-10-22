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
        self.assertIn(b"/home/.eclipse-sun//web", patched)

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


if __name__ == "__main__":
    unittest.main()
