# ProDSS Process App

This directory contains the packaged desktop Streamlit app for YSI ProDSS processing.

## Local run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Local package build

```bash
pip install -r requirements.txt
pyinstaller build.spec --noconfirm --clean
```
