import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import warnings

warnings.filterwarnings("ignore")

# Sovelluksen sivun asetukset
st.set_page_config(
    page_title="FinBalt Natural Gas Entry Flows",
    page_icon="🔥",
    layout="wide"
)

# --- 1. ENTSOG DATA: NOPEA JA TURVALLINEN OPERAATTORIHAKU ---

# Haetaan kaikki 3 maata puhtaasti virallisilla TSO-koodeilla, mikä pakottaa ENTSOGin 
# kunnioittamaan Physical Flow -rajausta (estää kapasiteettiroskan).
OPERATORS = [
    'FI-TSO-0001', # Gasgrid Finland (Sisältää Inkoon ja Haminan)
    'LV-TSO-0001', # Conexus Baltic Grid
    'LT-TSO-0001', # Amber Grid
]

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_entsog_operator_chunk(operator_key, from_str, to_str):
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    offset = 0
    limit = 5000
    chunk_records = []

    while True:
        params = {
            'indicator': 'Physical Flow',
            'from': from_str,
            'to': to_str,
            'limit': limit,
            'offset': offset,
            'directionKey': 'entry',
            'operatorKey': operator_key,
            'periodType': 'day'
        }

        try:
            response = requests.get(url, params=params, timeout=12)
            if response.status_code == 200:
                data = response.json().get('operationalData', [])
                if not data:
                    break
                chunk_records.extend(data)
                if len(data) < limit:
                    break
                offset += limit
            else:
                break
        except Exception:
            break

    return chunk_records


@st.cache_data(ttl=86400, show_spinner="Ladataan FinBalt gas entry -historiaa (24kk)...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    first_day_current_month = today.replace(day=1)
    start_dt = (first_day_current_month - timedelta(days=24 * 31)).replace(day=1)
    mid_dt = start_dt + timedelta(days=365)

    date_ranges = [
        (start_dt.strftime('%Y-%m-%d'), mid_dt.strftime('%Y-%m-%d')),
        ((mid_dt + timedelta(days=1)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    ]

    all_data = []

    for op in OPERATORS:
        for from_str, to_str in date_ranges:
            records = fetch_entsog_operator_chunk(op, from_str, to_str)
            all_data.extend(records)

    df = pd.DataFrame(all_data)
    
    if not df.empty:
        df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)

        if 'periodType' in df.columns:
            df = df[df['periodType'].astype(str).str.lower() == 'day']
            
        if 'directionKey' in df.columns:
            df = df[df['directionKey'].astype(str).str.lower() == 'entry']

        # Yksikkökorjaus: Jos API palauttaa tuntiarvoja, skaalataan ne vuorokausiarvoiksi
        unit_col = next((c for c in ['unitKey', 'unit'] if c in df.columns), None)
        if unit_col:
            is_hourly = df[unit_col].astype(str).str.lower().str.contains('/h')
            df.loc[is_hourly, 'value'] = df.loc[is_hourly, 'value'] * 24

        date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart', 'gasDayStartedOn'] if c in df.columns), None)
        if date_col:
            df['Clean_Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
            
            # Valitaan duplikaateista paras (uusin tai oikea Actual-tila) kapasiteettimaksimin sijaan
            if 'statusKey' in df.columns:
                df['is_actual'] = df['statusKey'].astype(str).str.lower() == 'actual'
            else:
                df['is_actual'] = True
                
            if 'version' in df.columns:
                df['ver_num'] = pd.to_numeric(df['version'], errors='coerce').fillna(0)
            else:
                df['ver_num'] = 1
                
            # Lajitellaan niin, että oikein ja uusin on viimeisenä, ja pudotetaan aiemmat
            df = df.sort_values(['pointKey', 'Clean_Date', 'is_actual', 'ver_num'], ascending=[True, True, True, True])
            df = df.drop_duplicates(subset=['pointKey', 'Clean_Date'], keep='last')

    return df


def classify_entry_flow(row):
    pk = str(row.get('pointKey', '')).upper()
    pl = str(row.get('pointLabel', '')).lower()

    # 1. Suomi: Täysin tiukka kohdistus suoraan Inkoon ja Haminan pistekoodeihin (ei päästä esim. Baltconnectoria tähän summaan)
    if pk in ['ITP-00495', 'ITP-00508']:
        return 'Inkoo & Hamina LNG'

    # 2. Latvia (Inčukalns)
    if 'incukalns' in pl or 'inčukalns' in pl:
        return 'Inčukalns UGS (Withdrawal)'

    # 3. Liettua (Klaipėda LNG)
    if 'klaip' in pl or 'independence' in pl or 'kn' in pl:
        if not ('gipl' in pl or 'santaka' in pl):
            return 'Klaipėda LNG'

    # 4. Liettua (GIPL)
    if 'gipl' in pl or 'santaka' in pl:
        return 'GIPL (Poland -> LT)'

    return None


# --- 2. KÄYTTÖLIITTYMÄ (STREAMLIT UI) ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_full_entsog_entry_history()

if df_raw.empty:
    st.warning("Ei saatu yhteyttä ENTSOG API-rajapintaan. Napsauta 'Clear Cache & Refresh'.")
else:
    df_raw['Category'] = df_raw.apply(classify_entry_flow, axis=1)
    df_filtered = df_raw.dropna(subset=['Category']).copy()

    if df_filtered.empty:
        st.warning("Syöttövirtoja ei löytynyt annetulta aikaväliltä.")
    else:
        df_filtered['Date_Parsed'] = pd.to_datetime(df_filtered['Clean_Date'], utc=True)
        df_filtered['Month'] = df_filtered['Date_Parsed'].dt.strftime('%Y-%m')

        monthly_summary = df_filtered.groupby(['Month', 'Category'])['value'].sum().reset_index()
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
        df_display = pivot_df.tail(months_to_show)

        latest_month = df_display.index[-1]
        latest_total = df_display.loc[latest_month].sum()

        st.subheader(f"Latest Month Overview ({latest_month})")
        m_cols = st.columns(len(categories_order) + 1)

        m_cols[0].metric(label="Total Supply", value=f"{latest_total:.3f} TWh")
        for idx, col in enumerate(categories_order):
            val = df_display.loc[latest_month, col]
            m_cols[idx + 1].metric(label=col, value=f"{val:.3f} TWh")

        st.markdown("---")

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
