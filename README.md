# Veck.io

A self-hosted copy of the official **Veck.io** Unity WebGL build.

- Original game: https://veck.io/
- Official build source: https://veck.io/Build/
- Streaming assets: https://veck.io/StreamingAssets/

## Automatic sync

`scripts/sync-veck.py` reads the live https://veck.io/ page on every workflow
run. It resolves the current `productVersion`, the four build file names, and
the `StreamingAssets/aa<version>/` directory, then downloads the exact official
bytes. The official server publishes the Unity payloads as `.data.br`,
`.framework.js.br`, and `.wasm.br`; the script requests identity encoding, so it
stores the uncompressed bytes the Unity loader expects on a plain static host.

For each run the script:

1. Downloads the loader, data, framework, wasm, Addressables catalog, catalog
   hash, settings, every local `local_*.bundle` referenced by the catalog, and
   the shared logo/background/CSS assets.
2. Verifies the WebAssembly magic, the `UnityWebData` header, the loader entry
   point, and every non-empty payload before touching `site/`.
3. Renders `site/index.html` from `templates/index.html` with the current file
   names and version.
4. Writes `site/build-manifest.json` with the version, source URL, size, and
   SHA-256 of every mirrored file.
5. Removes files from older builds, so a new version never leaves stale
   `Build/` or `StreamingAssets/` payloads behind.

`.github/workflows/sync-veck.yml` runs the sync hourly, on manual dispatch, and
when the sync script or template changes. It commits a new build and, for
public repositories, publishes `site/` to GitHub Pages.

The ad SDK is replaced by a small no-ad compatibility shim in
`templates/index.html`; the game build itself is unchanged.

Veck.io and its assets are the property of Legion Platforms. This mirror is not
affiliated with or endorsed by them.
