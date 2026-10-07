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

# --- 1. ENTSOG DATA: KOHDENNETTU JA OIKEIN SKAALAUTUVA HAKU ---

OPERATORS = [
    'LV-TSO-0001', # Conexus Baltic Grid (Inčukalns)
    'LT-TSO-0001', # Amber Grid (Klaipėda, GIPL)
]

FINLAND_POINTS = [
    'ITP-00495', # Inkoo FSRU
    'ITP-00508', # Hamina LNG
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

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_entsog_point_chunk(point_key, from_str, to_str):
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    params = {
        'indicator': 'Physical Flow',
        'from': from_str,
        'to': to_str,
        'limit': 5000,
        'directionKey': 'entry',
        'pointKey': point_key,
        'periodType': 'day'
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('operationalData', [])
    except Exception:
        pass
    return []


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

    # 1. Haetaan Baltia operaattoripohjaisesti
    for op in OPERATORS:
        for from_str, to_str in date_ranges:
            records = fetch_entsog_operator_chunk(op, from_str, to_str)
            all_data.extend(records)

    # 2. Haetaan Inkoo ja Hamina puhtaasti pistekoodeilla
    for point_key in FINLAND_POINTS:
        for from_str, to_str in date_ranges:
            records = fetch_entsog_point_chunk(point_key, from_str, to_str)
            all_data.extend(records)

    df = pd.DataFrame(all_data)
    
    # Tiukka puhdistus ja duplikaattien esto: varmistetaan, että samaa päivää ei lasketa kahteen kertaan
    if not df.empty:
        if 'periodType' in df.columns:
            df = df[df['periodType'].astype(str).str.lower() == 'day']
        
        # Poistetaan kaksoiskappaleet pointKeyn ja alkupäivän mukaan
        subset_cols = [c for c in ['pointKey', 'periodFrom', 'directionKey'] if c in df.columns]
        if subset_cols:
            df = df.drop_duplicates(subset=subset_cols, keep='first')

    return df


def classify_entry_flow(row):
    point_label = str(row.get('pointLabel', '')).lower()
    point_key = str(row.get('pointKey', '')).lower()
    operator_label = str(row.get('operatorLabel', '')).lower()
    combined = f"{point_label} {point_key} {operator_label}"

    # 1. Inčukalns-varasto (Latvia)
    if 'incukalns' in combined or 'inčukalns' in combined:
        return 'Inčukalns UGS (Withdrawal)'

    # 2. Klaipėda LNG (Liettua)
    if 'klaip' in combined or 'independence' in combined or 'kn' in combined:
        if not ('gipl' in combined or 'santaka' in combined):
            return 'Klaipėda LNG'

    # 3. GIPL (Puola -> Liettua)
    if 'gipl' in combined or 'santaka' in combined or 'poland' in combined:
        return 'GIPL (Poland -> LT)'

    # 4. Inkoo & Hamina LNG (Suomi - pistekoodit ITP-00495 ja ITP-00508)
    if 'itp-00495' in combined or 'itp-00508' in combined or 'inkoo' in combined or 'hamina' in combined or 'fsru' in combined:
        return 'Inkoo & Hamina LNG'

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
        date_candidates = ['periodFrom', 'gasDayStart', 'periodStart', 'gasDayStartedOn']
        date_col = next((c for c in date_candidates if c in df_filtered.columns), None)
        if not date_col:
            date_col = next((c for c in df_filtered.columns if 'period' in c.lower() or 'date' in c.lower()), None)

        df_filtered['value'] = pd.to_numeric(df_filtered['value'], errors='coerce').fillna(0)
        df_filtered['Date_Parsed'] = pd.to_datetime(df_filtered[date_col], utc=True)
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
