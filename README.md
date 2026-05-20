# CFL Tools Monorepo

Monorepo for internal UW-Madison Center for Limnology desktop tools.

## Structure

- `apps/`: each Streamlit desktop app with isolated dependencies and build spec
- `shared/`: optional shared Python utilities
- `.github/workflows/build.yml`: cross-platform release builds

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

- `v1.2.3`: build all apps on both OS targets
- `v1.2.3-prodss-process`: build only one app on both OS targets
