# CFL Tools Monorepo

Monorepo for internal UW-Madison Center for Limnology desktop tools.

## Structure

- `apps/`: each Streamlit desktop app with isolated dependencies and build spec
- `shared/`: optional shared Python utilities
- `.github/workflows/build.yml`: Windows build and release workflow

## App Contract

Each app directory should include:

- `streamlit_app.py`: main Streamlit UI logic
- `launcher.py`: desktop entrypoint that starts Streamlit from source or bundled mode
- `requirements.txt`: app runtime + build dependencies
- `build.spec`: PyInstaller one-dir build configuration

## Local Build Example

```bash
cd apps/prodss-process
python -m pip install -r requirements.txt
pyinstaller build.spec --noconfirm --clean
```

Output is written to `dist/<app-name>/`.

## Tagging Strategy

- Builds are run manually from GitHub Actions (web UI)
- `release_tag` in manual run controls release naming and asset upload

## Build On Push

```bash
git add .
git commit -m "Your change"
git push origin main
```

This only updates source code. It does not trigger builds automatically.

## Build From GitHub Web UI

1. Open the repository on GitHub.com.
2. Go to **Actions** -> **Build Desktop Apps**.
3. Click **Run workflow**.
4. Choose `app` (`all` or one app).
5. Optionally set `release_tag` (example: `v1.0.2-prodss-process`) to publish Release assets.
6. Click **Run workflow**.

## Build A Release Download

Build one app release:

```bash
git tag v1.0.0-prodss-process
git push origin v1.0.0-prodss-process
```

Build all app releases:

```bash
git tag v1.0.0
git push origin v1.0.0
```

These tag commands are optional. Manual Action runs with `release_tag` are the primary release path.

## No-Tag Release (GitHub Desktop Friendly)

If your GitHub Desktop does not support creating tags:

1. Push your commit to `main` from GitHub Desktop.
2. Open the repo on GitHub.com.
3. Go to **Actions** -> **Build Desktop Apps** -> **Run workflow**.
4. Choose `app` (`all` or one app).
5. Enter `release_tag` (example: `v1.0.2-prodss-process`) if you want a GitHub Release asset.
6. Click **Run workflow**.

If `release_tag` is left blank, the workflow only uploads Actions artifacts (no Release).
