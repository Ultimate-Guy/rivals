#!/usr/bin/env python3
"""Mirror the official Veck.io Unity WebGL build into this repository.

The script reads the live https://veck.io/ page, resolves the current build
file names and product version, downloads the exact official bytes (requesting
identity encoding so the server does not transparently Brotli-compress them),
and renders Rivals/index.html from templates/index.html.

Nothing is written to Rivals/ unless every download and verification succeeds.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "Rivals"
TEMPLATE = ROOT / "templates" / "index.html"
MANIFEST_NAME = "build-manifest.json"
OFFICIAL_ROOT = "https://veck.io/"
MAX_FILE_BYTES = 256 * 1024 * 1024
COSMETIC_FILES = ("logo-v2.webp", "bg-v2.webp", "main.css")
BUNDLE_PATTERN = re.compile(rb"local_[A-Za-z0-9_]+_[a-f0-9]{32}\.bundle")


class SyncError(RuntimeError):
    pass


def fetch(url: str, *, attempts: int = 4, timeout: int = 120) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Cosmic-Veck-Sync/1.0 (+https://github.com/Ultimate-Guy/rivals)",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise SyncError(f"{url} exceeded the {MAX_FILE_BYTES} byte safety limit")
                if not data:
                    raise SyncError(f"{url} returned an empty body")
                return data
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise SyncError(f"Could not download {url}: {last_error}")


def fetch_optional(url: str) -> bytes | None:
    try:
        return fetch(url, attempts=2)
    except SyncError:
        return None


def parse_build(html: str) -> dict[str, str]:
    def required(pattern: str, label: str) -> str:
        match = re.search(pattern, html, re.IGNORECASE)
        if not match:
            raise SyncError(f"The official page did not expose {label}")
        return match.group(1)

    return {
        "productVersion": required(r'productVersion\s*:\s*["\']([0-9]+(?:\.[0-9]+)+)["\']', "productVersion"),
        "loader": required(r'loaderUrl\s*=\s*buildUrl\s*\+\s*["\']/([^"\']+\.loader\.js)["\']', "loaderUrl"),
        "data": required(r'dataUrl\s*:\s*buildUrl\s*\+\s*["\']/([^"\']+)["\']', "dataUrl"),
        "framework": required(r'frameworkUrl\s*:\s*buildUrl\s*\+\s*["\']/([^"\']+)["\']', "frameworkUrl"),
        "code": required(r'codeUrl\s*:\s*buildUrl\s*\+\s*["\']/([^"\']+)["\']', "codeUrl"),
    }


def local_name(name: str) -> str:
    return name[:-3] if name.endswith(".br") else name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(build: dict[str, str]) -> dict[str, tuple[bytes, str]]:
    """Return local path -> (bytes, source URL) for every mirrored file."""
    files: dict[str, tuple[bytes, str]] = {}

    build_url = urljoin(OFFICIAL_ROOT, "Build/")
    for key in ("loader", "data", "framework", "code"):
        source_name = build[key]
        source_url = urljoin(build_url, source_name)
        files[f"Build/{local_name(source_name)}"] = (fetch(source_url), source_url)

    streaming_dir = "aa" + build["productVersion"]
    streaming_url = urljoin(OFFICIAL_ROOT, f"StreamingAssets/{streaming_dir}/")
    catalog_url = urljoin(streaming_url, "catalog.bin")
    catalog = fetch(catalog_url)
    files[f"StreamingAssets/{streaming_dir}/catalog.bin"] = (catalog, catalog_url)

    for name in ("catalog.hash", "settings.json"):
        url = urljoin(streaming_url, name)
        payload = fetch_optional(url)
        if payload is not None:
            files[f"StreamingAssets/{streaming_dir}/{name}"] = (payload, url)

    bundles = sorted({match.group(0).decode("ascii") for match in BUNDLE_PATTERN.finditer(catalog)})
    if not bundles:
        raise SyncError("The official Addressables catalog listed no local bundles")
    for bundle in bundles:
        url = urljoin(streaming_url, f"WebGL/{bundle}")
        files[f"StreamingAssets/{streaming_dir}/WebGL/{bundle}"] = (fetch(url), url)

    for name in COSMETIC_FILES:
        url = urljoin(OFFICIAL_ROOT, name)
        payload = fetch_optional(url)
        if payload is not None:
            files[name] = (payload, url)

    return files


def verify(files: dict[str, tuple[bytes, str]], build: dict[str, str]) -> None:
    code_path = "Build/" + local_name(build["code"])
    data_path = "Build/" + local_name(build["data"])
    framework_path = "Build/" + local_name(build["framework"])
    loader_path = "Build/" + local_name(build["loader"])

    if not files[code_path][0].startswith(b"\x00asm"):
        raise SyncError(f"{code_path} is not a WebAssembly module")
    if not files[data_path][0].startswith(b"UnityWebData"):
        raise SyncError(f"{data_path} is not a UnityWeb data file")
    if b"createUnityInstance" not in files[loader_path][0]:
        raise SyncError(f"{loader_path} does not contain the Unity loader entry point")
    if len(files[framework_path][0]) < 1024:
        raise SyncError(f"{framework_path} is unexpectedly small")
    for path, (payload, _) in files.items():
        if not payload:
            raise SyncError(f"{path} is empty")


def render_index(build: dict[str, str], template: str) -> str:
    replacements = {
        "__LOADER_FILE__": local_name(build["loader"]),
        "__DATA_FILE__": local_name(build["data"]),
        "__FRAMEWORK_FILE__": local_name(build["framework"]),
        "__CODE_FILE__": local_name(build["code"]),
        "__PRODUCT_VERSION__": build["productVersion"],
    }
    rendered = template
    for token, value in replacements.items():
        if token not in rendered:
            raise SyncError(f"templates/index.html is missing {token}")
        rendered = rendered.replace(token, value)
    if re.search(r"__[A-Z_]+__", rendered):
        raise SyncError("templates/index.html contains an unresolved placeholder")
    return rendered


def build_manifest(build: dict[str, str], files: dict[str, tuple[bytes, str]]) -> dict:
    return {
        "schemaVersion": 1,
        "productVersion": build["productVersion"],
        "streamingAssetsVersion": "aa" + build["productVersion"],
        "source": OFFICIAL_ROOT,
        "files": {
            path: {"sha256": sha256(payload), "size": len(payload), "source": url}
            for path, (payload, url) in sorted(files.items())
        },
    }


def existing_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def plan_changes(files: dict[str, tuple[bytes, str]], index_html: str, manifest: dict) -> list[str]:
    changed: list[str] = []
    for path, (payload, _) in files.items():
        if existing_bytes(SITE / path) != payload:
            changed.append(path)
    if existing_bytes(SITE / "index.html") != index_html.encode("utf-8"):
        changed.append("index.html")
    if existing_bytes(SITE / MANIFEST_NAME) != (json.dumps(manifest, indent=2) + "\n").encode("utf-8"):
        changed.append(MANIFEST_NAME)

    wanted = set(files)
    wanted.add("index.html")
    wanted.add(MANIFEST_NAME)
    for managed in ("Build", "StreamingAssets"):
        base = SITE / managed
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = path.relative_to(SITE).as_posix()
            if relative not in wanted:
                changed.append(relative)
    return sorted(set(changed))


def apply(files: dict[str, tuple[bytes, str]], index_html: str, manifest: dict) -> None:
    wanted = set(files)
    wanted.add("index.html")
    wanted.add(MANIFEST_NAME)
    for managed in ("Build", "StreamingAssets"):
        base = SITE / managed
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if path.relative_to(SITE).as_posix() not in wanted:
                path.unlink()
        for path in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()

    for path, (payload, _) in files.items():
        destination = SITE / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    (SITE / "index.html").write_text(index_html, encoding="utf-8")
    (SITE / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_output(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", default=None)
    args = parser.parse_args()

    try:
        if not TEMPLATE.is_file():
            raise SyncError("templates/index.html is missing")
        html = fetch(OFFICIAL_ROOT).decode("utf-8", errors="replace")
        build = parse_build(html)
        files = collect(build)
        verify(files, build)
        index_html = render_index(build, TEMPLATE.read_text(encoding="utf-8"))
        manifest = build_manifest(build, files)
        changed_paths = plan_changes(files, index_html, manifest)
        if changed_paths:
            apply(files, index_html, manifest)
    except SyncError as error:
        print(f"Veck.io sync failed: {error}", file=sys.stderr)
        write_output(args.github_output, {"changed": "false"})
        return 1

    write_output(
        args.github_output,
        {
            "changed": "true" if changed_paths else "false",
            "version": build["productVersion"],
            "streamingAssets": "aa" + build["productVersion"],
        },
    )
    print(f"Official Veck.io build {build['productVersion']} ({len(files)} files) verified.")
    if changed_paths:
        print("Updated files:")
        for path in changed_paths:
            print(f"  {path}")
    else:
        print("Mirror already matches the official build; no files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
