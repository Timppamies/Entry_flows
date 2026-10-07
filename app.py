import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import warnings

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="FinBalt Natural Gas Entry Flows",
    page_icon="🔥",
    layout="wide"
)

# --- 1. ENTSOG MÄÄRITTELYT (Pisteet) ---
# Käytetään toimivia pistekoodeja (pointKey)
ENTRY_POINTS = [
    {'pointKey': 'FI-TP-0001', 'category': 'Inkoo & Hamina LNG'},
    {'pointKey': 'FI-TP-0002', 'category': 'Inkoo & Hamina LNG'},
    {'pointKey': 'LT-TP-0001', 'category': 'Klaipėda LNG'},
    {'pointKey': 'LT-TP-0002', 'category': 'GIPL (Poland -> LT)'},
    {'pointKey': 'LV-TP-0001', 'category': 'Inčukalns UGS (Withdrawal)'}
]

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_entsog_point_entry(point_key, start_date_str, end_date_str):
    """
    Hakee täsmällisen pisteen entry-virrat ilman vääriä/tuntemattomia suodatinparametreja.
    Pyyntö palauttaa vain muutaman kilotavun ja kestää alle sekunnin.
    """
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    offset = 0
    limit = 5000
    all_records = []

    while True:
        params = {
            'indicator': 'Physical Flow',
            'from': start_date_str,
            'to': end_date_str,
            'limit': limit,
            'offset': offset,
            'directionKey': 'entry',
            'pointKey': point_key,
            'periodType': 'day'
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json().get('operationalData', [])
                if not data:
                    break
                all_records.extend(data)
                if len(data) < limit:
                    break
                offset += limit
            else:
                break
        except Exception:
            break

    return all_records


@st.cache_data(ttl=86400, show_spinner="Ladataan FinBalt gas entry -historiaa sekunneissa...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    first_day_current_month = today.replace(day=1)
    start_dt = (first_day_current_month - timedelta(days=24 * 31)).replace(day=1)

    start_date_str = start_dt.strftime('%Y-%m-%d')
    end_date_str = today.strftime('%Y-%m-%d')

    all_data = []

    # Tehdään 5 täsmähakua (yksi per piste)
    for target in ENTRY_POINTS:
        records = fetch_entsog_point_entry(target['pointKey'], start_date_str, end_date_str)
        for r in records:
            r['Category'] = target['category']
            all_data.append(r)

    return pd.DataFrame(all_data)


# --- 2. KÄYTTÖLIITTYMÄ (STREAMLIT UI) ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

# Datan haku
df_raw = fetch_full_entsog_entry_history()

if df_raw.empty:
    st.warning("Ei saatu yhteyttä ENTSOG API-rajapintaan. Napsauta 'Clear Cache & Refresh'.")
else:
    date_candidates = ['periodFrom', 'gasDayStart', 'periodStart', 'gasDayStartedOn']
    date_col = next((c for c in date_candidates if c in df_raw.columns), None)
    if not date_col:
        date_col = next((c for c in df_raw.columns if 'period' in c.lower() or 'date' in c.lower()), None)

    df_raw['value'] = pd.to_numeric(df_raw['value'], errors='coerce').fillna(0)
    df_raw['Date_Parsed'] = pd.to_datetime(df_raw[date_col], utc=True)
    df_raw['Month'] = df_raw['Date_Parsed'].dt.strftime('%Y-%m')

    # Aggregointi kuukausitasolle (kWh -> TWh muunnos: / 1e9)
    monthly_summary = df_raw.groupby(['Month', 'Category'])['value'].sum().reset_index()
    monthly_summary['Value_TWh'] = monthly_summary['value'] / 1e9

    pivot_df = monthly_summary.pivot(index='Month', columns='Category', values='Value_TWh').fillna(0)

    categories_order = [
        'Inčukalns UGS (Withdrawal)', 
        'GIPL (Poland -> LT)', 
        'Klaipėda LNG', 
        'Inkoo & Hamina LNG'
    ]

    for col in categories_order:
        if col not in pivot_df.columns:
            pivot_df[col] = 0.0

    pivot_df = pivot_df[categories_order]

    # Slider-rajaus muistista (0 ms)
    df_display = pivot_df.tail(months_to_show)

    latest_month = df_display.index[-1]
    latest_total = df_display.loc[latest_month].sum()

    # --- Metrics ---
    st.subheader(f"Latest Month Overview ({latest_month})")
    m_cols = st.columns(len(categories_order) + 1)

    m_cols[0].metric(label="Total Supply", value=f"{latest_total:.3f} TWh")
    for idx, col in enumerate(categories_order):
        val = df_display.loc[latest_month, col]
        m_cols[idx + 1].metric(label=col, value=f"{val:.3f} TWh")

    st.markdown("---")

    # --- Plotly Pylväskaavio ---
    st.subheader("Monthly Gas Supply by Route (TWh)")

    plot_df = df_display.reset_index().melt(id_vars='Month', var_name='Entry Route', value_name='TWh')

    fig = px.bar(
        plot_df, 
        x='Month', 
        y='TWh', 
        color='Entry Route',
        title=f"FinBalt Natural Gas Entry Flows (Last {months_to_show} Months)",
        labels={'TWh': 'Energy (TWh / month)', 'Month': 'Month'},
        template='plotly_white',
        color_discrete_sequence=px.colors.qualitative.Set2
    )

    fig.update_layout(
        barmode='stack',
        xaxis_tickangle=-45,
        legend_title_text='Supply Route',
        height=500,
        hovermode="x unified"
    )

    st.plotly_chart(fig, use_container_width=True)

    # --- Taulukko & CSV-lataus ---
    st.subheader("Data Summary Table")
    display_df = df_display.copy()
    display_df['Total (TWh)'] = display_df.sum(axis=1)

    st.dataframe(display_df.style.format("{:.3f}"), use_container_width=True)

    csv_data = display_df.to_csv().encode('utf-8')
    st.download_button(
        label="Download Data as CSV 📥",
        data=csv_data,
        file_name=f"finbalt_gas_entry_flows_{latest_month}.csv",
        mime="text/csv"
    )
