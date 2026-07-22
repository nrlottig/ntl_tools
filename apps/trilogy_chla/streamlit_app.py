"""Trilogy CHLA app with persistent standards and sample processing."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st


EXPECTED_STANDARD_COLUMNS = [
	"target_conc",
	"actual_concentration",
	"rep",
	"fb",
	"fa",
	"blank",
]


def app_data_dir() -> Path:
	home = Path.home()
	if sys.platform == "darwin":
		base = home / "Library" / "Application Support"
	elif sys.platform.startswith("win"):
		base = Path.home() / "AppData" / "Local"
	else:
		base = home / ".local" / "share"
	path = base / "ntl_tools" / "trilogy_chla"
	path.mkdir(parents=True, exist_ok=True)
	return path


def db_path() -> Path:
	return app_data_dir() / "standards.sqlite"


def get_conn() -> sqlite3.Connection:
	conn = sqlite3.connect(db_path())
	conn.row_factory = sqlite3.Row
	return conn


def init_db() -> None:
	conn = get_conn()
	try:
		conn.executescript(
			"""
			CREATE TABLE IF NOT EXISTS standards (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL,
				curve_date TEXT NOT NULL,
				header_flag INTEGER NOT NULL,
				raw_text TEXT NOT NULL,
				r_mean REAL,
				fs_mean REAL,
				r_sd REAL,
				fs_sd REAL,
				r_n INTEGER,
				fs_n INTEGER,
				r_outliers_removed INTEGER,
				fs_outliers_removed INTEGER,
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL,
				is_deleted INTEGER NOT NULL DEFAULT 0
			);

			CREATE TABLE IF NOT EXISTS standard_versions (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				standard_id INTEGER NOT NULL,
				version_number INTEGER NOT NULL,
				saved_at TEXT NOT NULL,
				payload_json TEXT NOT NULL,
				FOREIGN KEY (standard_id) REFERENCES standards(id)
			);

			CREATE TABLE IF NOT EXISTS audit_log (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				standard_id INTEGER,
				action TEXT NOT NULL,
				event_at TEXT NOT NULL,
				details_json TEXT,
				FOREIGN KEY (standard_id) REFERENCES standards(id)
			);
			"""
		)
		conn.commit()
	finally:
		conn.close()


def parse_tab_delimited(text: str, has_header: bool) -> pd.DataFrame:
	header = 0 if has_header else None
	df = pd.read_csv(io.StringIO(text), sep="\t", header=header)
	if has_header:
		missing = [c for c in EXPECTED_STANDARD_COLUMNS if c not in df.columns]
		if missing:
			raise ValueError(f"Missing required columns: {', '.join(missing)}")
		return df[EXPECTED_STANDARD_COLUMNS].copy()

	if df.shape[1] != len(EXPECTED_STANDARD_COLUMNS):
		raise ValueError(
			f"Expected {len(EXPECTED_STANDARD_COLUMNS)} columns without a header, got {df.shape[1]}."
		)
	df.columns = EXPECTED_STANDARD_COLUMNS
	return df


def compute_standard_metrics(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
	data = df.copy()
	for col in EXPECTED_STANDARD_COLUMNS:
		data[col] = pd.to_numeric(data[col], errors="coerce")

	data["fb_cor"] = data["fb"] - data["blank"]
	data["fa_cor"] = data["fa"] - data["blank"]

	data["R"] = np.where(data["fa_cor"] != 0, data["fb_cor"] / data["fa_cor"], np.nan)
	data["fs"] = np.where(
		data["fb_cor"] != 0,
		data["actual_concentration"] / data["fb_cor"],
		np.nan,
	)
	data.replace([np.inf, -np.inf], np.nan, inplace=True)

	summary_stats = (
		data.groupby("target_conc", dropna=False)
		.agg(
			n_obs=("target_conc", "size"),
			R_mean=("R", "mean"),
			R_sd=("R", "std"),
			fs_mean=("fs", "mean"),
			fs_sd=("fs", "std"),
		)
		.reset_index()
	)

	r_clean, r_outliers = remove_iqr_outliers(data["R"])
	fs_clean, fs_outliers = remove_iqr_outliers(data["fs"])

	final = {
		"r_mean": float(r_clean.mean(skipna=True)) if len(r_clean) else np.nan,
		"r_sd": float(r_clean.std(skipna=True)) if len(r_clean) else np.nan,
		"r_n": int(r_clean.notna().sum()),
		"r_outliers_removed": int(r_outliers),
		"fs_mean": float(fs_clean.mean(skipna=True)) if len(fs_clean) else np.nan,
		"fs_sd": float(fs_clean.std(skipna=True)) if len(fs_clean) else np.nan,
		"fs_n": int(fs_clean.notna().sum()),
		"fs_outliers_removed": int(fs_outliers),
	}

	return data, summary_stats, final


def remove_iqr_outliers(series: pd.Series) -> tuple[pd.Series, int]:
	s = pd.to_numeric(series, errors="coerce")
	q1 = s.quantile(0.25)
	q3 = s.quantile(0.75)
	iqr = q3 - q1

	if pd.isna(iqr):
		return s, 0

	lower = q1 - 1.5 * iqr
	upper = q3 + 1.5 * iqr
	mask = s.between(lower, upper) | s.isna()
	outliers = int((~mask).sum())
	return s.where(mask), outliers


def standards_df() -> pd.DataFrame:
	conn = get_conn()
	try:
		df = pd.read_sql_query(
			"""
			SELECT id, name, curve_date, r_mean, fs_mean, updated_at
			FROM standards
			WHERE is_deleted = 0
			ORDER BY updated_at DESC, id DESC
			""",
			conn,
		)
		return df
	finally:
		conn.close()


def get_standard(standard_id: int) -> sqlite3.Row | None:
	conn = get_conn()
	try:
		row = conn.execute(
			"SELECT * FROM standards WHERE id = ? AND is_deleted = 0", (standard_id,)
		).fetchone()
		return row
	finally:
		conn.close()


def write_audit(conn: sqlite3.Connection, standard_id: int | None, action: str, details: dict) -> None:
	conn.execute(
		"INSERT INTO audit_log (standard_id, action, event_at, details_json) VALUES (?, ?, ?, ?)",
		(standard_id, action, datetime.utcnow().isoformat(), json.dumps(details)),
	)


def write_version(conn: sqlite3.Connection, standard_id: int, payload: dict) -> None:
	version_num = conn.execute(
		"SELECT COALESCE(MAX(version_number), 0) + 1 FROM standard_versions WHERE standard_id = ?",
		(standard_id,),
	).fetchone()[0]
	conn.execute(
		"""
		INSERT INTO standard_versions (standard_id, version_number, saved_at, payload_json)
		VALUES (?, ?, ?, ?)
		""",
		(standard_id, version_num, datetime.utcnow().isoformat(), json.dumps(payload)),
	)


def save_standard(
	editing_id: int | None,
	name: str,
	curve_date: str,
	has_header: bool,
	raw_text: str,
	final: dict,
	summary: pd.DataFrame,
) -> int:
	now = datetime.utcnow().isoformat()
	conn = get_conn()
	try:
		if editing_id is None:
			cur = conn.execute(
				"""
				INSERT INTO standards (
					name, curve_date, header_flag, raw_text,
					r_mean, fs_mean, r_sd, fs_sd, r_n, fs_n,
					r_outliers_removed, fs_outliers_removed,
					created_at, updated_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					name,
					curve_date,
					int(has_header),
					raw_text,
					final["r_mean"],
					final["fs_mean"],
					final["r_sd"],
					final["fs_sd"],
					final["r_n"],
					final["fs_n"],
					final["r_outliers_removed"],
					final["fs_outliers_removed"],
					now,
					now,
				),
			)
			standard_id = int(cur.lastrowid)
			action = "create"
		else:
			conn.execute(
				"""
				UPDATE standards
				SET name = ?, curve_date = ?, header_flag = ?, raw_text = ?,
					r_mean = ?, fs_mean = ?, r_sd = ?, fs_sd = ?,
					r_n = ?, fs_n = ?, r_outliers_removed = ?, fs_outliers_removed = ?,
					updated_at = ?
				WHERE id = ?
				""",
				(
					name,
					curve_date,
					int(has_header),
					raw_text,
					final["r_mean"],
					final["fs_mean"],
					final["r_sd"],
					final["fs_sd"],
					final["r_n"],
					final["fs_n"],
					final["r_outliers_removed"],
					final["fs_outliers_removed"],
					now,
					editing_id,
				),
			)
			standard_id = int(editing_id)
			action = "edit"

		version_payload = {
			"name": name,
			"curve_date": curve_date,
			"header_flag": bool(has_header),
			"raw_text": raw_text,
			"final": final,
			"summary_stats": summary.to_dict(orient="records"),
			"saved_at": now,
		}
		write_version(conn, standard_id, version_payload)
		write_audit(conn, standard_id, action, {"name": name, "curve_date": curve_date})
		conn.commit()
		return standard_id
	finally:
		conn.close()


def get_versions(standard_id: int) -> pd.DataFrame:
	conn = get_conn()
	try:
		return pd.read_sql_query(
			"""
			SELECT version_number, saved_at
			FROM standard_versions
			WHERE standard_id = ?
			ORDER BY version_number DESC
			""",
			conn,
			params=(standard_id,),
		)
	finally:
		conn.close()


def get_audit(standard_id: int) -> pd.DataFrame:
	conn = get_conn()
	try:
		return pd.read_sql_query(
			"""
			SELECT action, event_at, details_json
			FROM audit_log
			WHERE standard_id = ?
			ORDER BY id DESC
			""",
			conn,
			params=(standard_id,),
		)
	finally:
		conn.close()


def parse_sample_text(text: str, has_header: bool) -> pd.DataFrame:
	header = 0 if has_header else None
	df = pd.read_csv(io.StringIO(text), sep="\t", header=header)

	if has_header:
		# Allow either legacy fb/fa names or the field-style RFUb/RFUa names.
		rename_map = {
			"RFUb": "rfub",
			"RFUa": "rfua",
			"Blank": "blank",
			"Dilution": "dilution",
			"Volume": "volume",
			"Lake": "lake",
			"Date": "date",
			"Depth": "depth",
			"Rep": "rep",
		}
		for old_name, new_name in rename_map.items():
			if old_name in df.columns and new_name not in df.columns:
				df = df.rename(columns={old_name: new_name})

		required = {"rfub", "rfua", "volume", "dilution"}
		missing = sorted(required - set(df.columns))
		if missing and {"fb", "fa"}.issubset(set(df.columns)):
			df = df.rename(columns={"fb": "rfub", "fa": "rfua"})
			missing = sorted(required - set(df.columns))
		if missing:
			raise ValueError(f"Missing required sample columns: {', '.join(missing)}")
		return df.copy()

	if df.shape[1] == 8:
		cols = [
			"lake",
			"date",
			"depth",
			"rep",
			"volume",
			"rfub",
			"rfua",
			"dilution",
		]
	elif df.shape[1] == 9:
		# Backward-compatible handling of an extra pasted blank column.
		cols = [
			"lake",
			"date",
			"depth",
			"rep",
			"volume",
			"rfub",
			"rfua",
			"blank_from_file",
			"dilution",
		]
	else:
		raise ValueError(
			"Expected 8 columns without header: Lake, Date, Depth, Rep, Volume, RFUb, RFUa, Dilution. "
			"(9 columns are also accepted if an extra blank column is present.)"
		)

	df.columns = cols
	return df


def compute_samples(
	df: pd.DataFrame,
	r_mean: float,
	fs_mean: float,
	blank_mean: float,
	extraction_volume_ml: float,
	clamp_negative: bool,
) -> pd.DataFrame:
	out = df.copy()
	out["rfub"] = pd.to_numeric(out["rfub"], errors="coerce")
	out["rfua"] = pd.to_numeric(out["rfua"], errors="coerce")
	# Blank correction always uses the average of the two blank inputs.
	out["blank"] = float(blank_mean)

	out["dilution"] = pd.to_numeric(out["dilution"], errors="coerce")
	out["filtered_volume_ml"] = pd.to_numeric(out["volume"], errors="coerce").replace(0, np.nan)

	out["extraction_volume_ml"] = float(extraction_volume_ml)

	out["fb_cor"] = out["rfub"] - out["blank"]
	out["fa_cor"] = out["rfua"] - out["blank"]
	out["acid_ratio"] = np.where(out["fa_cor"] != 0, out["fb_cor"] / out["fa_cor"], np.nan)

	denom = r_mean - 1.0
	if np.isclose(denom, 0.0):
		out["scale_factor"] = np.nan
	else:
		out["scale_factor"] = (
			(r_mean / denom)
			* fs_mean
			* out["dilution"]
			* (out["extraction_volume_ml"] / out["filtered_volume_ml"])
		)

	out["corrected_chla_ug_l"] = out["scale_factor"] * (out["fb_cor"] - out["fa_cor"])
	out["pheophytin_ug_l"] = out["scale_factor"] * ((r_mean * out["fa_cor"]) - out["fb_cor"])
	# Total CHLA (uncorrected for acid ratio): ((RFUb_blank_corrected) * slope * extract_vol * dilution) / filtered_vol
	out["total_chla_ug_l"] = (
		out["fb_cor"] * fs_mean * out["extraction_volume_ml"] * out["dilution"]
	) / out["filtered_volume_ml"]

	if clamp_negative:
		out["corrected_chla_ug_l"] = out["corrected_chla_ug_l"].clip(lower=0)
		out["total_chla_ug_l"] = out["total_chla_ug_l"].clip(lower=0)

	out.replace([np.inf, -np.inf], np.nan, inplace=True)
	return out


def standard_label(row: pd.Series) -> str:
	return (
		f"{int(row['id'])} | {row['name']} | {row['curve_date']} "
		f"| R={row['r_mean']:.4f} | fs={row['fs_mean']:.4f}"
	)


def init_state() -> None:
	defaults = {
		"std_name": "",
		"std_curve_date": datetime.now().strftime("%Y%m%d"),
		"std_has_header": False,
		"std_raw_text": "",
		"std_parsed": None,
		"std_calculated": None,
		"std_summary": None,
		"std_final": None,
		"editing_standard_id": None,
		"sample_text": "",
		"sample_has_header": True,
		"sample_result": None,
	}
	for key, value in defaults.items():
		if key not in st.session_state:
			st.session_state[key] = value


def clear_editor_state() -> None:
	st.session_state["editing_standard_id"] = None
	st.session_state["std_name"] = ""
	st.session_state["std_curve_date"] = datetime.now().strftime("%Y%m%d")
	st.session_state["std_has_header"] = False
	st.session_state["std_raw_text"] = ""
	st.session_state["std_parsed"] = None
	st.session_state["std_calculated"] = None
	st.session_state["std_summary"] = None
	st.session_state["std_final"] = None


def load_selected_standard_into_editor() -> None:
	selected_standard_id = st.session_state.get("selected_standard_id")
	if selected_standard_id is None:
		return
	row = get_standard(int(selected_standard_id))
	if row is None:
		st.session_state["std_ui_message"] = "Could not load selected standard."
		return

	st.session_state["editing_standard_id"] = int(row["id"])
	st.session_state["std_name"] = row["name"]
	st.session_state["std_curve_date"] = row["curve_date"]
	st.session_state["std_has_header"] = bool(row["header_flag"])
	st.session_state["std_raw_text"] = row["raw_text"]

	parsed = parse_tab_delimited(row["raw_text"], bool(row["header_flag"]))
	calc, summary, final = compute_standard_metrics(parsed)
	st.session_state["std_parsed"] = parsed
	st.session_state["std_calculated"] = calc
	st.session_state["std_summary"] = summary
	st.session_state["std_final"] = final
	st.session_state["std_ui_message"] = "Loaded standard into editor."


st.set_page_config(page_title="Trilogy CHLA", layout="wide")
st.title("Trilogy CHLA")
st.caption("Standards are persisted locally on this computer and can be reused across sessions.")

init_db()
init_state()

tabs = st.tabs(["Standards", "Samples"])

with tabs[0]:
	st.subheader("Standards")
	standards = standards_df()

	manage_col, data_col = st.columns([1, 2])

	with manage_col:
		st.markdown("Saved standards")
		if standards.empty:
			st.info("No saved standards yet.")
			selected_standard_id = None
		else:
			id_to_label = {
				int(standards.iloc[i]["id"]): standard_label(standards.iloc[i])
				for i in range(len(standards))
			}
			selected_standard_id = st.selectbox(
				"Select standard",
				options=list(id_to_label.keys()),
				format_func=lambda x: id_to_label[x],
				key="selected_standard_id",
			)

			st.button(
				"Load selected into editor",
				use_container_width=True,
				on_click=load_selected_standard_into_editor,
			)

			if selected_standard_id is not None:
				versions = get_versions(selected_standard_id)
				audit = get_audit(selected_standard_id)
				with st.expander("Version history", expanded=False):
					st.dataframe(versions, use_container_width=True, hide_index=True)
				with st.expander("Audit history", expanded=False):
					st.dataframe(audit, use_container_width=True, hide_index=True)

	with data_col:
		st.markdown("Standard input")
		if st.session_state.get("std_ui_message"):
			st.success(st.session_state["std_ui_message"])
			st.session_state["std_ui_message"] = ""
		st.text_input("Standard name", key="std_name", placeholder="Example: 2026 main curve")
		st.text_input("Curve date (YYYYMMDD)", key="std_curve_date")
		st.checkbox("Data has header row", key="std_has_header")
		st.text_area(
			"Paste tab-delimited standard data",
			key="std_raw_text",
			height=220,
			placeholder="target_conc\tactual_concentration\trep\tfb\tfa\tblank",
		)

		c1, c2, c3 = st.columns(3)
		if c1.button("Process standard", use_container_width=True):
			try:
				parsed = parse_tab_delimited(
					st.session_state["std_raw_text"], st.session_state["std_has_header"]
				)
				calc, summary, final = compute_standard_metrics(parsed)
				st.session_state["std_parsed"] = parsed
				st.session_state["std_calculated"] = calc
				st.session_state["std_summary"] = summary
				st.session_state["std_final"] = final
				st.success("Standard calculations completed.")
			except Exception as exc:
				st.error(f"Could not process standard data: {exc}")

		c2.button("Clear editor", use_container_width=True, on_click=clear_editor_state)

		save_label = "Update standard" if st.session_state["editing_standard_id"] else "Save new standard"
		if c3.button(save_label, use_container_width=True):
			final = st.session_state.get("std_final")
			summary = st.session_state.get("std_summary")
			if final is None or summary is None:
				st.error("Process the standard data before saving.")
			elif not st.session_state["std_name"].strip():
				st.error("Provide a standard name before saving.")
			else:
				try:
					saved_id = save_standard(
						editing_id=st.session_state["editing_standard_id"],
						name=st.session_state["std_name"].strip(),
						curve_date=st.session_state["std_curve_date"].strip(),
						has_header=bool(st.session_state["std_has_header"]),
						raw_text=st.session_state["std_raw_text"],
						final=final,
						summary=summary,
					)
					st.session_state["editing_standard_id"] = saved_id
					st.success(f"Standard saved (id={saved_id}).")
				except Exception as exc:
					st.error(f"Save failed: {exc}")

	final = st.session_state.get("std_final")
	summary = st.session_state.get("std_summary")
	calculated = st.session_state.get("std_calculated")
	parsed = st.session_state.get("std_parsed")

	if final is not None:
		st.markdown("Final values used downstream")
		m1, m2, m3, m4 = st.columns(4)
		m1.metric("r_mean", f"{final['r_mean']:.6f}")
		m2.metric("fs_mean", f"{final['fs_mean']:.6f}")
		m3.metric("r_outliers_removed", final["r_outliers_removed"])
		m4.metric("fs_outliers_removed", final["fs_outliers_removed"])

	if summary is not None:
		st.markdown("Summary by target concentration")
		st.dataframe(summary, use_container_width=True, hide_index=True)

	if calculated is not None:
		with st.expander("Calculated rows", expanded=False):
			st.dataframe(calculated, use_container_width=True, hide_index=True)

	if parsed is not None and calculated is not None and final is not None:
		export_df = pd.DataFrame(
			{
				"metric": ["r_mean", "r_sd", "r_n", "fs_mean", "fs_sd", "fs_n"],
				"value": [
					final["r_mean"],
					final["r_sd"],
					final["r_n"],
					final["fs_mean"],
					final["fs_sd"],
					final["fs_n"],
				],
			}
		)

		out = io.BytesIO()
		with pd.ExcelWriter(out, engine="openpyxl") as writer:
			export_df.to_excel(writer, index=False, sheet_name="Final_Results")
			calculated.to_excel(writer, index=False, sheet_name="All_Data")
			parsed.to_excel(writer, index=False, sheet_name="Raw_Data")
		out.seek(0)

		st.download_button(
			"Download standard analysis record (xlsx)",
			data=out.getvalue(),
			file_name=f"{st.session_state['std_curve_date']}_standardcurve.xlsx",
			mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		)

with tabs[1]:
	st.subheader("Samples")
	standards = standards_df()

	if standards.empty:
		st.warning("Create at least one saved standard first.")
	else:
		labels = [standard_label(row) for _, row in standards.iterrows()]
		idx_to_id = {labels[i]: int(standards.iloc[i]["id"]) for i in range(len(labels))}
		selected_label = st.selectbox("Standard to use", options=labels, key="sample_standard")
		standard_id = idx_to_id[selected_label]
		row = get_standard(standard_id)

		st.write(
			f"Using standard id {standard_id}: r_mean={row['r_mean']:.6f}, fs_mean={row['fs_mean']:.6f}"
		)
		st.caption("Uses standard-file values: r_mean as acid-ratio mean and fs_mean as slope.")

		inputs_col, formula_col = st.columns([1, 1.35], vertical_alignment="top")

		with inputs_col:
			st.markdown("**Inputs**")
			b1, b2 = st.columns(2)
			with b1:
				blank_1 = st.number_input("Blank 1", value=0.0, step=0.1, format="%.6f")
			with b2:
				blank_2 = st.number_input("Blank 2", value=0.0, step=0.1, format="%.6f")
			blank_mean = (blank_1 + blank_2) / 2.0
			st.caption(f"Average blank used in calculations: {blank_mean:.6f}")

			extraction_volume_ml = st.number_input(
				"Extraction volume (mL)",
				min_value=0.001,
				value=25.0,
				step=0.5,
				format="%.3f",
			)

			st.checkbox("Sample data has header row", key="sample_has_header")
			st.text_area(
				"Paste tab-delimited sample data",
				key="sample_text",
				height=200,
				placeholder="Lake\tDate\tDepth\tRep\tVolume\tRFUb\tRFUa\tDilution",
			)
			clamp_negative = st.checkbox("Clamp negative outputs to zero", value=True)

		with formula_col:
			st.markdown("**Equations**")
			st.latex(
				r"Corrected\ Chla = \left(\frac{R}{R-1}\right)\times Slope\times(F_b^{cor}-F_a^{cor})\times Dilution\times\frac{V_{extract}}{V_{filtered}}"
			)
			st.latex(
				r"Pheophytin = \left(\frac{R}{R-1}\right)\times Slope\times((R\times F_a^{cor})-F_b^{cor})\times Dilution\times\frac{V_{extract}}{V_{filtered}}"
			)
			st.latex(
				r"Total\ Chla = \frac{F_b^{cor}\times Slope\times V_{extract}\times Dilution}{V_{filtered}}"
			)

		if st.button("Process samples", use_container_width=True):
			try:
				sample_df = parse_sample_text(
					st.session_state["sample_text"], st.session_state["sample_has_header"]
				)
				sample_result = compute_samples(
					sample_df,
					r_mean=float(row["r_mean"]),
					fs_mean=float(row["fs_mean"]),
					blank_mean=float(blank_mean),
					extraction_volume_ml=float(extraction_volume_ml),
					clamp_negative=clamp_negative,
				)
				st.session_state["sample_result"] = sample_result
				st.success("Sample calculations completed.")
			except Exception as exc:
				st.error(f"Could not process sample data: {exc}")

		sample_result = st.session_state.get("sample_result")
		if sample_result is not None:
			export_result = sample_result.drop(columns=["scale_factor"], errors="ignore")
			calc_cols = ["total_chla_ug_l", "corrected_chla_ug_l", "pheophytin_ug_l"]
			base_cols = [c for c in export_result.columns if c not in calc_cols]
			export_result = export_result[base_cols + [c for c in calc_cols if c in export_result.columns]]

			m1, m2, m3 = st.columns(3)
			m1.metric("Rows", f"{len(sample_result):,}")
			m2.metric(
				"Mean corrected_chla_ug_l",
				f"{sample_result['corrected_chla_ug_l'].mean(skipna=True):.6f}",
			)
			m3.metric(
				"Mean total_chla_ug_l",
				f"{sample_result['total_chla_ug_l'].mean(skipna=True):.6f}",
			)
			st.dataframe(export_result, use_container_width=True, hide_index=True)

			st.download_button(
				"Download sample results CSV",
				data=export_result.to_csv(index=False).encode("utf-8"),
				file_name="trilogy_chla_sample_results.csv",
				mime="text/csv",
			)
