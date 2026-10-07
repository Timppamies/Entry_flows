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

# --- 1. ENTSOG TÄSMÄMÄÄRITYKSET ---
# Avaimet ja koodit ENTSOG Transparency Platformin mukaisesti
ENTRY_CONFIG = [
    {
        'category': 'Inkoo & Hamina LNG',
        'operatorKey': 'FI-TSO-0001',
        'pointKey': 'ITP-00495'  # Inkoo FSRU
    },
    {
        'category': 'Inkoo & Hamina LNG',
        'operatorKey': 'FI-TSO-0001',
        'pointKey': 'ITP-00508'  # Hamina LNG
    },
    {
        'category': 'Klaipėda LNG',
        'operatorKey': 'LT-TSO-0001',
        'pointKey': 'ITP-00163'  # Klaipėda LNG Terminal
    },
    {
        'category': 'GIPL (Poland -> LT)',
        'operatorKey': 'LT-TSO-0001',
        'pointKey': 'ITP-00500'  # Santaka / GIPL
    },
    {
        'category': 'Inčukalns UGS (Withdrawal)',
        'operatorKey': 'LV-TSO-0001',
        'pointKey': 'ITP-00160'  # Inčukalns UGS
    }
]

def fetch_chunk(operator_key, point_key, from_date, to_date):
    """
    Hakee yhtä tiettyä ajanjaksoa ENTSOG operationaldatas-rajapinnasta.
    """
    url = "https://transparency.entsog.eu/api/v1/operationaldatas.json"
    params = {
        'indicator': 'Physical Flow',
        'from': from_date,
        'to': to_date,
        'limit': -1,
        'directionKey': 'entry',
        'operatorKey': operator_key,
        'pointKey': point_key,
        'periodType': 'day'
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        if r.status_code == 200:
            res = r.json()
            return res.get('operationaldatas', res.get('operationalData', []))
    except Exception:
        pass
    return []

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_single_point_history(operator_key, point_key, start_date_str, end_date_str):
    """
    Pilkkoo 24kk haun 6 kuukauden palasiin estääkseen ENTSOG API 60s timeout -virheet.
    """
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    
    all_data = []
    curr_start = start_dt
    
    # Haetaan max 180 päivää kerrallaan
    while curr_start < end_dt:
        curr_end = min(curr_start + timedelta(days=180), end_dt)
        chunk_data = fetch_chunk(
            operator_key, 
            point_key, 
            curr_start.strftime('%Y-%m-%d'), 
            curr_end.strftime('%Y-%m-%d')
        )
        all_data.extend(chunk_data)
        curr_start = curr_end + timedelta(days=1)
        
    return all_data

@st.cache_data(ttl=86400, show_spinner="Ladataan FinBalt-kaasutietoja ENTSOG-rajapinnasta...")
def fetch_all_entry_flows():
    today = datetime.today()
    first_day_current_month = today.replace(day=1)
    start_dt = (first_day_current_month - timedelta(days=24 * 31)).replace(day=1)

    start_date_str = start_dt.strftime('%Y-%m-%d')
    end_date_str = today.strftime('%Y-%m-%d')

    all_records = []

    for item in ENTRY_CONFIG:
        records = fetch_single_point_history(
            item['operatorKey'], 
            item['pointKey'], 
            start_date_str, 
            end_date_str
        )
        for r in records:
            r['Category'] = item['category']
            all_records.append(r)

    return pd.DataFrame(all_records)


# --- 2. STREAMLIT KÄYTTÖLIITTYMÄ ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_all_entry_flows()

if df_raw.empty:
    st.error("Ei saatu yhteyttä ENTSOG API-rajapintaan tai vastaukset olivat tyhjiä. Kokeile 'Clear Cache & Refresh'.")
else:
    date_candidates = ['periodFrom', 'gasDayStart', 'periodStart', 'gasDayStartedOn']
    date_col = next((c for c in date_candidates if c in df_raw.columns), None)
    
    if not date_col:
        st.error("Päivämääräsarakaetta ei tunnistettu vastausdatasta.")
    else:
        df_raw['value'] = pd.to_numeric(df_raw['value'], errors='coerce').fillna(0)
        df_raw['Date_Parsed'] = pd.to_datetime(df_raw[date_col], utc=True)
        df_raw['Month'] = df_raw['Date_Parsed'].dt.strftime('%Y-%m')

        # kWh -> TWh muunnos (/ 1e9)
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

        # --- Plotly Graph ---
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

        # --- Table & Download ---
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
