"""FinBalt Regional Gas Supply: entry flows into Finland and the Baltics (ENTSOG).

Data is fetched every evening by GitHub Actions into data/entsog_daily.csv
(fetch_data.py), so the app normally reads it from the repository and calls
the ENTSOG API only if that file is missing or out of date.
Run with:  streamlit run app.py
"""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from entsog import SERIES
from store import fetch_live, load_flows, source_text

st.set_page_config(page_title="FinBalt Gas Supply", page_icon="⛽", layout="wide")

COLUMNS = {  # group -> display name
    "INC": "Inčukalns UGS (Withdrawal)",
    "GIPL": "GIPL Import (PL → LT)",
    "KLP": "Klaipėda LNG",
    "FI_LNG": "Inkoo + Hamina LNG (FI)",
}
SUPPLY_COLS = list(COLUMNS.values())

# Manual Inčukalns withdrawal values (TWh) for months where ENTSOG reports zero.
INC_OVERRIDES = {
    "2025-01": 2.2,
    "2025-02": 3.4,
    "2025-03": 1.3,
    "2025-04": 1.1,
}


# ---------------------------------------------------------------- data load
def load_all(start, end, force_live=False):
    """Stored data (updated every evening), topped up live from ENTSOG if needed."""
    flows, failed_keys, dupes, info = load_flows(start, end, force_live)
    failed = [f"Stored file: {failed_keys[None]}"] if None in failed_keys else []
    frames = []
    for grp, op, pt, d, label in SERIES:
        if (op, pt, d) in failed_keys:
            failed.append(f"{label} [{op} {pt} {d}]: {failed_keys[(op, pt, d)]}")
        days = flows.get((op, pt, d), {})
        if days:
            frames.append(pd.DataFrame({
                "day": list(days.keys()),
                "TWh": [v / 1e9 for v in days.values()],  # kWh/d -> TWh
                "group": grp,
                "series": f"{COLUMNS[grp]}: {label}",
            }))
    if frames:
        raw = pd.concat(frames, ignore_index=True)
    else:
        raw = pd.DataFrame(columns=["day", "TWh", "group", "series"])
    return raw, failed, dupes, info


def build_monthly(raw):
    raw = raw.copy()
    raw["Month"] = raw["day"].str[:7]
    g = raw.pivot_table(index="Month", columns="group", values="TWh", aggfunc="sum")
    g = g.reindex(columns=list(COLUMNS)).fillna(0.0)
    g = g.rename(columns=COLUMNS).sort_index()
    for month, value in INC_OVERRIDES.items():
        if month in g.index:
            g.loc[month, COLUMNS["INC"]] = value
    return g


# ---------------------------------------------------------------------- UI
st.title("⛽ FinBalt Regional Gas Supply")
st.markdown(
    "Monthly gas **supply (entry flows)** into the Finland and Baltic market from the "
    "ENTSOG Transparency Platform (daily Physical Flow): Inčukalns storage withdrawal, "
    "GIPL import from Poland, Klaipėda LNG and Finnish LNG (Inkoo + Hamina)."
)

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Time period (months):", 3, 24, 12, 1)
if st.sidebar.button("Fetch latest from ENTSOG 🔄"):
    fetch_live.clear()
    # Kept for the session, so the live data stays when other settings change.
    st.session_state["force_live"] = True
if st.sidebar.button("Clear cache"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(
    "Data is fetched from ENTSOG into the repository every evening, so the app "
    "opens without waiting for the API. ENTSOG publishes each gas day on the "
    "following day."
)
force_live = st.session_state.get("force_live", False)

today = date.today()
start_date = (pd.Timestamp(today.replace(day=1)) - pd.DateOffset(months=24)).date()
# Stored history starts on 1 January of the window's first year.
raw, failed, dupes, info = load_all(date(start_date.year, 1, 1), today, force_live)

if failed:
    st.warning(
        "Some ENTSOG series could not be loaded, so the figures below may be "
        "incomplete. Press \"Fetch latest from ENTSOG\" to retry."
    )
    with st.expander("Failed series"):
        for f in failed:
            st.write(f)

if raw.empty:
    st.error("No flow data retrieved from ENTSOG.")
    st.stop()

monthly = build_monthly(raw)
df_display = monthly.tail(months_to_show)[SUPPLY_COLS]

last_day = date.fromisoformat(raw["day"].max())
latest = df_display.index[-1]
month_end = pd.Period(latest, freq="M").end_time.date()
partial = last_day < month_end

# --- KPI cards
label = f"{latest} (partial, data through {last_day})" if partial else latest
st.subheader(f"Latest month overview: {label}")
row = df_display.loc[latest]
k = st.columns(5)
k[0].metric("Total supply", f"{row.sum():.1f} TWh")
k[1].metric("Inčukalns withdrawal", f"{row[COLUMNS['INC']]:.1f} TWh")
k[2].metric("GIPL", f"{row[COLUMNS['GIPL']]:.1f} TWh")
k[3].metric("Klaipėda LNG", f"{row[COLUMNS['KLP']]:.1f} TWh")
k[4].metric("Inkoo + Hamina LNG", f"{row[COLUMNS['FI_LNG']]:.1f} TWh")

st.markdown("---")

# --- Manual values note
if any(m in INC_OVERRIDES for m in df_display.index):
    st.info(
        "Inčukalns withdrawal for January-April 2025 uses manually entered values "
        "(2.2 / 3.4 / 1.3 / 1.1 TWh), because ENTSOG reports zero for those months."
    )

# --- Chart
st.subheader("Monthly supply by source (TWh)")
plot_df = (df_display.reset_index()
           .rename(columns={"index": "Month"})
           .melt(id_vars="Month", var_name="Source", value_name="TWh"))
fig = px.bar(
    plot_df, x="Month", y="TWh", color="Source",
    title=f"FinBalt gas supply by source (last {months_to_show} months)",
    labels={"TWh": "Energy (TWh / month)"},
    template="plotly_white",
)
fig.update_layout(barmode="stack", xaxis_tickangle=-45, height=520,
                  legend_title_text="Source", hovermode="x unified")
fig.update_yaxes(tickformat=".1f")
st.plotly_chart(fig, use_container_width=True)

# --- Table and download
st.subheader("Summary table (TWh)")
table = df_display.copy()
table["Total supply"] = table.sum(axis=1)
st.dataframe(table.style.format("{:.1f}"), use_container_width=True)

st.download_button(
    "Download CSV 📥",
    table.round(1).to_csv().encode("utf-8"),
    file_name=f"finbalt_gas_supply_{latest}.csv",
    mime="text/csv",
)

# --- Method and data quality
with st.expander("Method and caveats"):
    st.markdown(
        """
**Source:** ENTSOG Transparency Platform, indicator *Physical Flow*, daily values
(kWh/d) at *entry* points, summed per month and converted to TWh.

- **Inčukalns UGS (Withdrawal):** `UGS-00029`, Conexus (LV-TSO-0001), entry.
  January-April 2025 are hardcoded (2.2 / 3.4 / 1.3 / 1.1 TWh) because ENTSOG
  reports zeros for those months. The Data quality tables show the raw ENTSOG values.
- **GIPL Import:** Santaka `ITP-00556`, Amber Grid (LT-TSO-0001), entry (Poland → Lithuania).
- **Klaipėda LNG:** `LNG-00030`, Amber Grid (LT-TSO-0001), entry.
- **Inkoo + Hamina LNG:** `LNG-00072` and `LNG-00011`, Gasgrid Finland (FI-TSO-0003), entry.
- Not included: Russian pipeline imports (no flows in the period), Balticconnector and
  Kiemenai (flows between countries inside the market), and domestic production
  (Latvian production is small, around 0.01-0.03 TWh per month).
- Storage withdrawal is a supply source only in the sense that it releases gas
  that was injected earlier; injection is shown in the demand app.
- The latest month is incomplete until the month ends.
- The whole period is re-fetched every evening, so ENTSOG's revisions to old
  days show up the next day.
        """
    )

with st.expander("Data quality"):
    st.write(f"Duplicate daily records dropped: {dupes}")
    st.write(source_text(info))
    tail_months = sorted(raw["day"].str[:7].unique())[-months_to_show:]
    sub = raw[raw["day"].str[:7].isin(tail_months)].copy()
    sub["Month"] = sub["day"].str[:7]
    st.markdown("**Days of data per month and series**")
    st.dataframe(sub.groupby(["Month", "series"]).size().unstack("series").fillna(0).astype(int),
                 use_container_width=True)
    st.markdown("**Monthly TWh per series**")
    st.dataframe(sub.pivot_table(index="Month", columns="series", values="TWh",
                                 aggfunc="sum").fillna(0).style.format("{:.3f}"),
                 use_container_width=True)
