"""
YSI ProDSS Analyzer - Streamlit Version
Processes YSI ProDSS multiparameter sensor export files (tab-delimited).
Standardizes headers, coalesces duplicate sensor columns, filters by site,
bins observations to target depths, and averages within-minute replicates.
"""

import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import io
import sys
import subprocess

st.set_page_config(page_title="YSI ProDSS Analyzer", layout="wide")
st.title("YSI ProDSS Analyzer")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fixed metadata columns (no sensor suffix)
METADATA_COLS = {
    "TIME": "time",
    "DATE": "date",
    "FILE NAME": "file_name",
    "SITE NAME": "site_name",
    "USER ID": "user_id",
    "FAULT CODE": "fault_code",
}

# Unit string → clean suffix
UNIT_MAP = {
    "∞C": "c", "°C": "c",
    "µG/L": "ugl", "µg/L": "ugl", "UG/L": "ugl",
    "µS/CM": "uscm", "µS/cm": "uscm", "US/CM": "uscm",
    "MG/L": "mgl", "mg/L": "mgl",
    "RFU": "rfu",
    "FNU": "fnu", "NTU": "ntu",
    "PSU": "psu", "PPT": "ppt",
    "MV": "mv", "mV": "mv",
    "MMHG": "mmhg", "mmHg": "mmhg",
    "PSI A": "psia",
    "∞": "deg", "°": "deg",
    "% SAT": "pctsat", "%SAT": "pctsat",
    "M": "m",
}

# Parameter name → clean name
PARAM_MAP = {
    "gps_latitude": "gps_lat",
    "gps_longitude": "gps_lon",
    "sp_cond": "sp_cond",
    "vertical_position": "vert_pos",
}

# GPS columns — drop unit suffix (implied degrees)
GPS_COLS = {"gps_lat", "gps_lon"}


# ---------------------------------------------------------------------------
# Header standardization
# ---------------------------------------------------------------------------

def normalize_unit(unit_str):
    """Map a raw unit string to a clean short suffix."""
    unit_str = unit_str.strip()
    if unit_str in UNIT_MAP:
        return UNIT_MAP[unit_str]
    # Fallback: lowercase, remove special/non-ascii chars
    u = unit_str.lower()
    u = u.replace("∞", "deg").replace("°", "deg")
    u = u.replace("µ", "u").replace("μ", "u")
    u = u.replace("%", "pct").replace("/", "").replace(" ", "").replace(".", "")
    return u


def standardize_col_name(col):
    """
    Convert a raw YSI column header to a clean snake_case name.

    Handles three patterns:
      1. Bare metadata:  TIME, DATE, SITE NAME, etc.
      2. Param (Unit)-SensorID:  TEMP (∞C)-24K104444  →  temp_c
      3. Param-SensorID (no unit):  PH-24F100182  →  ph
    """
    col = col.strip()

    # Fixed metadata columns
    if col in METADATA_COLS:
        return METADATA_COLS[col]

    # Strip sensor ID: everything after the last hyphen
    if "-" in col:
        col_no_sensor = col.rsplit("-", 1)[0].strip()
    else:
        col_no_sensor = col

    # Extract PARAMETER (UNIT) if present
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", col_no_sensor)
    if m:
        param_raw = m.group(1).strip()
        unit_raw  = m.group(2).strip()
        unit_std  = normalize_unit(unit_raw)
    else:
        param_raw = col_no_sensor
        unit_std  = None

    # Normalize parameter name to snake_case
    param = param_raw.lower()
    param = re.sub(r"[^a-z0-9]+", "_", param).strip("_")
    param = PARAM_MAP.get(param, param)

    # GPS columns: no unit suffix
    if param in GPS_COLS:
        return param

    return f"{param}_{unit_std}" if unit_std else param


def coalesce_duplicate_columns(df):
    """
    For each group of columns sharing the same standardized name,
    produce a single column by taking the first non-null value per row.
    """
    seen: dict[str, list[int]] = {}
    for i, col in enumerate(df.columns):
        seen.setdefault(col, []).append(i)

    result = {}
    for col, indices in seen.items():
        if len(indices) == 1:
            result[col] = df.iloc[:, indices[0]]
        else:
            series = df.iloc[:, indices[0]].copy()
            for idx in indices[1:]:
                series = series.combine_first(df.iloc[:, idx])
            result[col] = series

    return pd.DataFrame(result)


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def detect_delimiter(raw_bytes, encoding):
    """
    Sniff the delimiter from the first line of the file.
    Tries tab first (YSI default), then comma, then semicolon.
    Falls back to tab if sniffer fails.
    """
    try:
        first_line = raw_bytes.decode(encoding).split("\n")[0]
        tab_count   = first_line.count("\t")
        comma_count = first_line.count(",")
        semi_count  = first_line.count(";")
        counts = {"\t": tab_count, ",": comma_count, ";": semi_count}
        best = max(counts, key=counts.get)
        # Only use the sniffed delimiter if it actually splits the header
        return best if counts[best] > 1 else "\t"
    except Exception:
        return "\t"


def load_and_standardize(source):
    """
    Load a YSI ProDSS export (tab- or comma-delimited).
    `source` may be a file path string or a file-like object (from st.file_uploader).
    Returns (standardized_df, raw_col_names, standardized_col_names).
    """
    df_raw = None
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            if hasattr(source, "read"):
                source.seek(0)
                raw_bytes = source.read()
                sep = detect_delimiter(raw_bytes, encoding)
                df_raw = pd.read_csv(
                    io.StringIO(raw_bytes.decode(encoding)),
                    sep=sep, header=0, dtype=str, on_bad_lines="skip",
                )
            else:
                with open(source, "rb") as f:
                    raw_bytes = f.read()
                sep = detect_delimiter(raw_bytes, encoding)
                df_raw = pd.read_csv(
                    io.StringIO(raw_bytes.decode(encoding)),
                    sep=sep, header=0, dtype=str, on_bad_lines="skip",
                )
            # Sanity check: if we got ≤ 2 columns the delimiter was wrong
            if len(df_raw.columns) <= 2:
                df_raw = None
                continue
            break
        except Exception:
            continue

    if df_raw is None:
        raise ValueError("Could not read file — tried utf-8, latin-1, and cp1252 encodings.")

    raw_cols = df_raw.columns.tolist()
    std_cols = [standardize_col_name(c) for c in raw_cols]
    df_raw.columns = std_cols

    # Coalesce duplicate columns (same param, different sensor IDs)
    df = coalesce_duplicate_columns(df_raw)

    # Convert everything except known text columns to numeric
    text_cols = {"time", "date", "file_name", "site_name", "user_id", "fault_code"}
    for col in df.columns:
        if col not in text_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Parse datetime from TIME + DATE columns
    if "time" in df.columns and "date" in df.columns:
        combined = (
            df["date"].astype(str).str.strip() + " " +
            df["time"].astype(str).str.strip()
        )
        df["datetime"] = pd.NaT
        # Try formats in order of likelihood
        for fmt in (
            "%m/%d/%y %I:%M:%S %p",   # 4/24/26 2:54:13 PM
            "%m/%d/%Y %I:%M:%S %p",   # 4/24/2026 2:54:13 PM
            "%m/%d/%y %H:%M:%S",      # 4/24/26 14:54:13
            "%m/%d/%Y %H:%M:%S",      # 4/24/2026 14:54:13
            "%Y-%m-%d %H:%M:%S",      # 2026-04-24 14:54:13
            "%Y-%m-%d %I:%M:%S %p",   # 2026-04-24 2:54:13 PM
        ):
            if df["datetime"].isna().any():
                parsed = pd.to_datetime(combined, format=fmt, errors="coerce")
                df["datetime"] = df["datetime"].fillna(parsed)
        # Last resort: let pandas infer
        if df["datetime"].isna().all():
            df["datetime"] = pd.to_datetime(combined, errors="coerce")
    else:
        df["datetime"] = pd.NaT

    return df, raw_cols, std_cols, sep


# ---------------------------------------------------------------------------
# Depth binning
# ---------------------------------------------------------------------------

def build_depth_bins(interval, max_depth):
    """Return array of target depth values: 0, interval, 2*interval, … max_depth."""
    return np.round(np.arange(0, max_depth + interval * 0.01, interval), 6)


def assign_depth_bin(depth_series, interval, max_depth):
    """
    Map each raw depth value to the nearest target bin.
    Values more than half an interval beyond max_depth are dropped (NaN),
    so with max_depth=32 and interval=1, anything > 32.5 m is excluded.
    Values clearly above the surface (< -0.5 m) are also dropped.
    """
    bins   = build_depth_bins(interval, max_depth)
    cutoff = max_depth + interval * 0.5

    def _bin(val):
        if pd.isna(val) or val < -0.5 or val > cutoff:
            return np.nan
        idx = int(np.argmin(np.abs(bins - val)))
        return float(bins[idx])

    return depth_series.apply(_bin)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def assign_replicate_clusters(df, gap_minutes):
    """
    Within each (site_name, depth_bin_m) group, assign an integer cluster ID.
    A new cluster starts whenever the gap between consecutive readings (sorted
    by datetime) exceeds `gap_minutes`. This correctly handles:
      - readings that straddle a minute boundary (e.g. 1:59:58 and 2:00:10)
      - two separate profiles on the same lake/date separated by hours
    Returns df with a 'cluster_id' column added (globally unique integers).
    """
    df = df.sort_values(["site_name", "depth_bin_m", "datetime"]).copy()

    cluster_id  = 0
    cluster_ids = []
    prev_site   = None
    prev_depth  = None
    prev_time   = None

    for _, row in df.iterrows():
        site  = row["site_name"]
        depth = row["depth_bin_m"]
        t     = row["datetime"]

        new_group = (site != prev_site) or (depth != prev_depth)

        if new_group:
            cluster_id += 1
        elif prev_time is not None and pd.notna(t) and pd.notna(prev_time):
            gap_sec = (t - prev_time).total_seconds()
            if gap_sec > gap_minutes * 60:
                cluster_id += 1

        cluster_ids.append(cluster_id)
        prev_site  = site
        prev_depth = depth
        prev_time  = t

    df["cluster_id"] = cluster_ids
    return df


def process_data(df, selected_sites, site_settings, gap_minutes):
    """
    Filter → bin depths (per-site settings) → gap-based replicate clustering → average.
    site_settings: dict of {site_name: {"interval": float, "max_depth": float}}
    Returns a tidy DataFrame with one row per averaged sample.
    """
    if "site_name" not in df.columns:
        raise ValueError("No 'site_name' column found after standardization.")
    if "depth_m" not in df.columns:
        raise ValueError("No 'depth_m' column found after standardization. "
                         "Expected a 'DEPTH (M)' column in the source file.")
    if "datetime" not in df.columns or df["datetime"].isna().all():
        sample_time = df["time"].dropna().iloc[0] if "time" in df.columns and not df["time"].dropna().empty else "N/A"
        sample_date = df["date"].dropna().iloc[0] if "date" in df.columns and not df["date"].dropna().empty else "N/A"
        raise ValueError(
            f"Could not parse datetime from TIME and DATE columns. "
            f"Sample values — TIME: '{sample_time}', DATE: '{sample_date}'. "
            f"Please report these values so the format can be added."
        )

    # Filter to selected sites
    df_f = df[df["site_name"].isin(selected_sites)].copy()
    if df_f.empty:
        raise ValueError("No data rows found for the selected sites.")

    # Apply per-site depth binning
    bin_parts = []
    for site in selected_sites:
        cfg       = site_settings[site]
        interval  = cfg["interval"]
        max_depth = cfg["max_depth"]
        site_df   = df_f[df_f["site_name"] == site].copy()
        site_df["depth_bin_m"] = assign_depth_bin(site_df["depth_m"], interval, max_depth)
        bin_parts.append(site_df)

    df_f = pd.concat(bin_parts, ignore_index=True)
    df_f = df_f[df_f["depth_bin_m"].notna()].copy()
    if df_f.empty:
        raise ValueError(
            "No observations mapped to any depth bin across the selected sites. "
            "Check that Interval and Max Depth values match your data."
        )

    # Gap-based replicate clustering
    df_f = assign_replicate_clusters(df_f, gap_minutes)

    # Representative datetime per cluster (earliest reading)
    cluster_start = (
        df_f.groupby("cluster_id")["datetime"]
            .min()
            .rename("sample_datetime")
    )
    df_f = df_f.join(cluster_start, on="cluster_id")

    # -----------------------------------------------------------------------
    # Group and average
    # -----------------------------------------------------------------------
    group_keys = ["site_name", "date", "depth_bin_m", "cluster_id", "sample_datetime"]
    group_keys = [k for k in group_keys if k in df_f.columns]

    skip_avg = set(group_keys) | {
        "time", "file_name", "user_id", "fault_code", "datetime",
    }
    numeric_cols = [
        c for c in df_f.columns
        if c not in skip_avg and pd.api.types.is_numeric_dtype(df_f[c])
    ]

    agg_dict = {col: "mean" for col in numeric_cols}
    if "time" in df_f.columns:
        agg_dict["time"] = "first"

    grouped = df_f.groupby(group_keys, sort=False)
    result  = grouped.agg(agg_dict).reset_index()

    # Replicate count as a separate operation then merge in
    n_reps = grouped.size().reset_index(name="n_reps")
    result = result.merge(n_reps, on=group_keys, how="left")

    # Round numeric output
    for col in numeric_cols:
        if col in result.columns:
            result[col] = result[col].round(4)

    result = result.drop(columns=["cluster_id"], errors="ignore")

    meta_first = [c for c in ["site_name", "date", "time", "sample_datetime",
                               "depth_bin_m", "n_reps"] if c in result.columns]
    rest = [c for c in result.columns if c not in meta_first]
    result = result[meta_first + rest]

    sort_by = [c for c in ["site_name", "date", "depth_bin_m", "sample_datetime"]
               if c in result.columns]
    result = result.sort_values(sort_by).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# Main Streamlit app
# ---------------------------------------------------------------------------

def main():
    st.sidebar.header("Input Settings")

    # ── File selection ──────────────────────────────────────────────────────
    # Primary: drag-and-drop / browser upload (works on all platforms)
    uploaded_file = st.sidebar.file_uploader(
        "Upload YSI export file",
        type=["txt", "csv", "tsv"],
        help="Drag and drop or click to browse. Accepts tab-delimited .txt, .csv, or .tsv.",
    )

    # Secondary: paste a local path (also enables "Save to Source Folder")
    st.sidebar.markdown("— or paste a local file path —")
    manual_path = st.sidebar.text_input(
        "File Path:",
        value=st.session_state.get("manual_path", ""),
        placeholder="/path/to/your/ysi_export.txt",
    )
    if manual_path:
        st.session_state["manual_path"] = manual_path

    output_filename = st.sidebar.text_input("Output Filename:", value="ysi_processed.csv")

    # ── Determine active source and load ───────────────────────────────────
    # Uploaded file takes priority; fall back to manual path.
    df = None
    raw_cols, std_cols = [], []
    file_path = None   # local path, if known (enables "Save to folder")

    # Build a cache key to avoid re-parsing on every Streamlit rerun
    if uploaded_file is not None:
        cache_key = f"upload:{uploaded_file.name}:{uploaded_file.size}"
        source    = uploaded_file
    elif manual_path and os.path.isfile(manual_path):
        cache_key = f"path:{manual_path}:{os.path.getmtime(manual_path)}"
        source    = manual_path
        file_path = manual_path
    elif manual_path:
        st.sidebar.error("File not found — check the path.")
        cache_key = None
        source    = None
    else:
        cache_key = None
        source    = None

    if source is not None:
        if st.session_state.get("loaded_key") != cache_key:
            try:
                with st.spinner("Loading and standardizing columns…"):
                    df, raw_cols, std_cols, sep = load_and_standardize(source)
                    st.session_state.update(
                        df=df, raw_cols=raw_cols, std_cols=std_cols,
                        loaded_key=cache_key, file_path=file_path,
                        detected_sep=sep,
                    )
            except Exception as e:
                st.sidebar.error(f"Load error: {e}")
        else:
            df        = st.session_state.get("df")
            raw_cols  = st.session_state.get("raw_cols", [])
            std_cols  = st.session_state.get("std_cols", [])
            file_path = st.session_state.get("file_path")

    # ── Process button (sidebar, bottom) ────────────────────────────────────
    process_clicked = st.sidebar.button(
        "⚗️  Process Data", type="primary", use_container_width=True
    )

    # ── Two-column layout ───────────────────────────────────────────────────
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Settings")

        if df is not None:
            sep_label = {"\\t": "tab", "\t": "tab", ",": "comma", ";": "semicolon"}.get(
                st.session_state.get("detected_sep", "\t"), "tab"
            )
            st.success(f"✅ {len(df):,} rows · {len(df.columns)} columns loaded ({sep_label}-delimited)")

            # Site selection
            if "site_name" in df.columns:
                sites = sorted(df["site_name"].dropna().unique().tolist())
                selected_sites = st.multiselect(
                    "Sites to process:",
                    options=sites,
                    default=sites,
                    help="Selects rows matching these SITE NAME values.",
                )
                st.session_state["selected_sites"] = selected_sites
            else:
                st.warning("No SITE NAME column detected.")
                selected_sites = []

            # Per-site depth settings
            st.markdown("**Depth Settings by Site**")
            if selected_sites:
                prev_settings = st.session_state.get("site_settings", {})

                # Header row
                h1, h2, h3 = st.columns([2, 1, 1])
                h1.caption("Site")
                h2.caption("Interval (m)")
                h3.caption("Max Depth (m)")

                site_settings = {}
                for site in selected_sites:
                    prev = prev_settings.get(site, {"interval": 1.0, "max_depth": 10.0})
                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.markdown(f"**{site}**")
                    with c2:
                        iv = st.number_input(
                            "Interval", min_value=0.1, max_value=20.0,
                            value=float(prev["interval"]), step=0.5, format="%.1f",
                            key=f"iv_{site}", label_visibility="collapsed",
                        )
                    with c3:
                        md = st.number_input(
                            "Max depth", min_value=0.5, max_value=200.0,
                            value=float(prev["max_depth"]), step=1.0, format="%.1f",
                            key=f"md_{site}", label_visibility="collapsed",
                        )
                    site_settings[site] = {"interval": iv, "max_depth": md}

                st.session_state["site_settings"] = site_settings
            else:
                site_settings = {}
                st.caption("Select sites above to configure depth settings.")

            # Replicate clustering
            st.markdown("**Replicate Averaging**")
            gap_minutes = st.number_input(
                "Max gap between replicates (min):",
                min_value=1, max_value=120, value=5, step=1,
                help=(
                    "Readings at the same depth bin are grouped as replicates as long as "
                    "consecutive readings are no more than this many minutes apart. "
                    "Increase if a single profile takes longer than expected; "
                    "decrease if two profiles on the same lake/day are being merged."
                ),
            )

            # Column mapping expander
            with st.expander("🔤 Column name mapping", expanded=False):
                st.dataframe(
                    pd.DataFrame({"Original": raw_cols, "Standardized": std_cols}),
                    use_container_width=True, hide_index=True,
                )

            # Raw data preview
            with st.expander("👁 Raw data preview (first 10 rows)", expanded=False):
                st.dataframe(df.head(10), use_container_width=True)

        else:
            site_settings = st.session_state.get("site_settings", {})
            st.info("Select a YSI ProDSS export file to begin.")

    # ── Results column ──────────────────────────────────────────────────────
    with col2:
        st.subheader("Results")

        if process_clicked:
            if df is None:
                st.error("No file loaded — select a file first.")
            elif not selected_sites:
                st.error("Select at least one site to process.")
            else:
                try:
                    with st.spinner("Processing…"):
                        result = process_data(
                            df,
                            selected_sites,
                            site_settings,
                            gap_minutes,
                        )
                        st.session_state["result"] = result

                    n_sites  = result["site_name"].nunique() if "site_name" in result.columns else "?"
                    n_depths = result["depth_bin_m"].nunique() if "depth_bin_m" in result.columns else "?"
                    avg_reps = result["n_reps"].mean() if "n_reps" in result.columns else None

                    st.success(
                        f"✅ {len(result):,} averaged observations — "
                        f"{n_sites} site(s), {n_depths} depth bin(s)"
                        + (f", avg {avg_reps:.1f} replicates/bin" if avg_reps else "")
                    )

                    # Warn about any depth bins with only 1 replicate
                    if "n_reps" in result.columns:
                        singletons = (result["n_reps"] == 1).sum()
                        if singletons:
                            st.warning(
                                f"⚠️ {singletons} depth-bin(s) had only 1 reading "
                                f"(no averaging possible). Consider widening the time window "
                                f"or reviewing those rows."
                            )

                except Exception as e:
                    st.error(f"Processing error: {e}")

        # ── Display stored result ───────────────────────────────────────────
        if "result" in st.session_state and st.session_state["result"] is not None:
            result = st.session_state["result"]

            # Summary metrics
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Rows out", len(result))
            if "depth_bin_m" in result.columns:
                m2.metric("Depth bins", result["depth_bin_m"].nunique())
            if "site_name" in result.columns:
                m3.metric("Sites", result["site_name"].nunique())
            if "n_reps" in result.columns:
                m4.metric("Avg reps", f"{result['n_reps'].mean():.1f}")

            st.dataframe(result, use_container_width=True, hide_index=True)

            # ── Save options ────────────────────────────────────────────────
            st.subheader("Save Results")

            # Output folder: default to same folder as source file if known
            default_out = os.path.dirname(file_path) if file_path else ""
            out_folder = st.text_input(
                "Output folder:",
                value=st.session_state.get("out_folder", default_out),
                placeholder="Paste folder path, or leave blank to use Downloads",
            )
            if out_folder:
                st.session_state["out_folder"] = out_folder

            cs1, cs2 = st.columns(2)

            with cs1:
                # Save to specified folder
                if out_folder and os.path.isdir(out_folder):
                    save_path = os.path.join(out_folder, output_filename)
                    if st.button("💾 Save to Folder", use_container_width=True):
                        try:
                            result.to_csv(save_path, index=False, na_rep="")
                            st.success(f"✅ Saved to:\n`{save_path}`")
                            st.session_state["last_save_path"] = save_path
                        except Exception as e:
                            st.error(f"Save error: {e}")
                    st.caption(f"→ {save_path}")

                    # Reveal in Finder / Explorer after save
                    if st.session_state.get("last_save_path"):
                        if st.button("📂 Reveal in Finder", use_container_width=True):
                            folder = os.path.dirname(st.session_state["last_save_path"])
                            try:
                                if sys.platform == "darwin":
                                    subprocess.run(["open", folder])
                                elif sys.platform == "win32":
                                    subprocess.run(["explorer", folder])
                                else:
                                    subprocess.run(["xdg-open", folder])
                            except Exception as e:
                                st.error(f"Could not open folder: {e}")
                elif out_folder:
                    st.error("Folder not found — check the path.")
                else:
                    st.info("Enter an output folder path above to enable direct saving.")

            with cs2:
                csv_bytes = result.to_csv(index=False, na_rep="").encode()
                st.download_button(
                    label="📥 Download to Downloads",
                    data=csv_bytes,
                    file_name=output_filename,
                    mime="text/csv",
                    use_container_width=True,
                )
                st.caption("Saves to your browser Downloads folder")


if __name__ == "__main__":
    main()
