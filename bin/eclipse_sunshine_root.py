#!/usr/bin/env python3
"""Privileged part of "Sunshine for Gaming Mode" (see eclipse/sunshine_native.py).

The Eclipse backend runs it with the user's sudo password:

    sudo python3 -I eclipse_sunshine_root.py install [--tag v2026.914.233613]
    sudo python3 -I eclipse_sunshine_root.py uninstall

Everything installed is downloaded and unpacked here, as root, into a fresh
root-owned directory. Nothing the unprivileged user can write ends up in it,
except the helpers shipped next to this script in the (root-owned) plugin.
Progress goes to stdout, one line per step. Install lines end with the overall
progress, e.g. "Unpacking... (77%)", which the plugin shows as its progress bar.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "py_modules"))

from eclipse import sunshine_native as sn  # noqa: E402

# First line printed: tells the backend that sudo accepted the password.
READY = "ECLIPSE-ROOT-READY"
HELPERS_DIR = os.path.join(HERE, "native")
CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")
MAX_DOWNLOAD = 256 * 1024 * 1024


class Failure(Exception):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def step(percent: int, message: str) -> None:
    """A step of the install, with the overall progress of the whole install."""
    say(f"{message} ({percent}%)")


def untrusted_files() -> list[str]:
    """Plugin files run or installed by this script that someone other than root could change."""
    paths = [os.path.realpath(__file__), sn.__file__, os.path.join(os.path.dirname(sn.__file__), "__init__.py")]
    paths += [os.path.join(HELPERS_DIR, name) for name in sn.HELPERS]
    bad = []
    for path in paths:
        for item in (path, os.path.dirname(path)):
            try:
                st = os.stat(item)
            except OSError:
                bad.append(item)
                continue
            if st.st_uid != 0 or st.st_mode & 0o022:
                bad.append(item)
    return sorted(set(bad))


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    for bundle in CA_BUNDLES:
        if os.path.isfile(bundle):
            context.load_verify_locations(bundle)
            break
    # Ignore proxy variables: a broken one in the session would stop the install.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))


def fetch(url: str, label: str = "", span: tuple[int, int] = (0, 0), attempts: int = 3) -> bytes:
    """Download ``url``. With ``label``, prints progress mapped onto the overall ``span`` (start, end percent)."""
    request = urllib.request.Request(url, headers={"User-Agent": "DeckyEclipse", "Accept": "*/*"})
    last_error: Exception = Failure("no attempt")
    for attempt in range(1, attempts + 1):
        try:
            with _opener().open(request, timeout=60) as res:
                total = int(res.headers.get("Content-Length") or 0)
                chunks, done, shown = [], 0, -1
                total_mb = total // (1024 * 1024)
                while chunk := res.read(256 * 1024):
                    chunks.append(chunk)
                    done += len(chunk)
                    if done > MAX_DOWNLOAD:
                        raise Failure(f"{url} is larger than expected")
                    if label and total:
                        overall = span[0] + (span[1] - span[0]) * min(done, total) // total
                        if overall > shown:
                            shown = overall
                            step(overall, f"Downloading {label}: {done // (1024 * 1024)} of {total_mb} MB")
                return b"".join(chunks)
        except Failure:
            raise
        except Exception as exc:  # noqa: BLE001 - network errors come in many types
            last_error = exc
            if attempt < attempts:
                say(f"Download failed ({exc}), retrying...")
                time.sleep(3 * attempt)
    raise Failure(f"Could not download {url}: {last_error}")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if check and res.returncode != 0:
        raise Failure(f"{' '.join(cmd)} failed: {(res.stderr or res.stdout).strip()}")
    return res


def missing_libraries(root: str) -> list[str]:
    """Libraries the Sunshine binary in ``root`` cannot find, checked as the calling user."""
    cmd = ["env", f"LD_LIBRARY_PATH={root}/lib", "ldd", f"{root}/bin/sunshine"]
    user = os.environ.get("SUDO_USER")
    if user and shutil.which("runuser"):
        cmd = ["runuser", "-u", user, "--", *cmd]
    res = run(cmd, check=False)
    return [line.strip() for line in (res.stdout + res.stderr).splitlines() if "not found" in line]


def install(tag: str | None) -> None:
    step(1, "Looking up the Sunshine release...")
    release = json.loads(fetch(sn.release_api_url(tag)).decode())
    asset = sn.pick_deb_asset(release)
    if not asset:
        raise Failure(f"Sunshine {release.get('tag_name') or tag} has no Ubuntu 22.04 build")
    deb = fetch(asset["url"], label=f"Sunshine {asset['version']}", span=(2, 70))

    libs = []
    for index, (path, digest) in enumerate(sn.UBUNTU_LIBS):
        name = os.path.basename(path)
        step(71 + 3 * index, f"Downloading {name}...")
        data = fetch(sn.UBUNTU_MIRROR + path)
        if sn.sha256(data) != digest:
            raise Failure(f"Checksum mismatch for {name}")
        libs.append(data)

    step(77, "Unpacking...")
    # Same file system as ROOT, so the final step is an atomic rename.
    stage = tempfile.mkdtemp(prefix=".eclipse-sun.", dir=os.path.dirname(sn.ROOT))
    try:
        system_files: dict[str, bytes] = {}
        with sn.deb_data(deb) as tar:
            if not sn.extract(tar, sn.sunshine_files(tar), stage):
                raise Failure("The Sunshine package has unexpected contents")
            wanted = dict(sn.SYSTEM_FILES)
            for member in tar.getmembers():
                path = sn.member_path(member)
                if path in wanted and member.isfile():
                    source = tar.extractfile(member)
                    if source:
                        system_files[wanted[path]] = source.read()
        for data in libs:
            with sn.deb_data(data) as tar:
                sn.extract(tar, sn.library_files(tar), stage)

        step(81, "Preparing Sunshine for Gaming Mode...")
        binary = os.path.join(stage, "bin", "sunshine")
        with open(binary, "rb") as fh:
            patched, places = sn.patch_assets_prefix(fh.read())
        if not places:
            raise Failure("This Sunshine build is not supported (asset path not found)")
        with open(binary, "wb") as fh:
            fh.write(patched)

        for name, dest, mode in (
            ("eclipse-sunshine", "bin/eclipse-sunshine", 0o755),
            ("libeclipse-sunshine.so", "lib/libeclipse-sunshine.so", 0o644),
        ):
            target = os.path.join(stage, dest)
            shutil.copyfile(os.path.join(HELPERS_DIR, name), target)
            os.chmod(target, mode)

        with open(os.path.join(stage, "eclipse.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": asset["version"], "tag": asset["tag"], "installedAt": int(time.time())}, fh)

        # Root-owned (created by root) and writable by root only; the
        # launcher refuses to pass on its capabilities otherwise.
        for directory, dirs, files in os.walk(stage):
            os.chmod(directory, 0o755)
            for name in files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, os.stat(path).st_mode & 0o755)

        step(85, "Checking libraries...")
        missing = missing_libraries(stage)
        if missing:
            raise Failure("Sunshine needs libraries this device lacks: " + "; ".join(missing))

        step(89, "Allowing Sunshine to capture the screen...")
        run(["setcap", sn.CAPABILITIES, os.path.join(stage, "bin", "eclipse-sunshine")])

        step(92, "Putting the new version in place...")
        old = sn.ROOT + ".old"
        if os.path.lexists(old):
            shutil.rmtree(old)
        if os.path.lexists(sn.ROOT):
            os.rename(sn.ROOT, old)
        os.rename(stage, sn.ROOT)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    step(94, "Setting up virtual controllers...")
    for dest, data in system_files.items():
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        os.chmod(dest, 0o644)
    # Virtual input still works for the most part without these; not fatal.
    run(["modprobe", "uhid"], check=False)
    run(["udevadm", "control", "--reload-rules"], check=False)
    for match in ("--property-match=DEVNAME=/dev/uinput", "--property-match=DEVNAME=/dev/uhid",
                  "--subsystem-match=hidraw", "--subsystem-match=input"):
        run(["udevadm", "trigger", match], check=False)
    step(96, f"Installed Sunshine {asset['version']}")


def uninstall() -> None:
    # The udev rules stay: the Flatpak build of Sunshine installs the same file.
    if os.path.lexists(sn.ROOT):
        shutil.rmtree(sn.ROOT)
    say("Removed Sunshine for Gaming Mode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install_cmd = commands.add_parser("install")
    install_cmd.add_argument("--tag", help="Sunshine release tag (default: the latest release)")
    commands.add_parser("uninstall")
    args = parser.parse_args()

    say(READY)
    if os.geteuid() != 0:
        say("ERROR: must run as root")
        return 1
    untrusted = untrusted_files()
    if untrusted:
        say("ERROR: these Eclipse files are not owned by root, reinstall the plugin: " + ", ".join(untrusted))
        return 1
    try:
        if args.command == "install":
            install(args.tag)
        else:
            uninstall()
    except (Failure, OSError, ValueError) as exc:
        say(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
