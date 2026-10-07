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

# --- 1. BALTIAN HAKU (ENTSOG) ---
OPERATORS = [
    'LV-TSO-0001', # Conexus Baltic Grid
    'LT-TSO-0001', # Amber Grid
]

def get_baltic_category(row):
    pl = str(row.get('pointLabel', '')).lower()
    if 'incukalns' in pl or 'inčukalns' in pl:
        return 'Inčukalns UGS (Withdrawal)'
    if 'klaip' in pl or 'independence' in pl or 'kn' in pl:
        if not ('gipl' in pl or 'santaka' in pl):
            return 'Klaipėda LNG'
    if 'gipl' in pl or 'santaka' in pl:
        return 'GIPL (Poland -> LT)'
    return None

@st.cache_data(ttl=3600, show_spinner="Noudetaan Baltian dataa ENTSOGista...")
def fetch_baltic_data():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    date_ranges = [
        (from_str, (start_dt + timedelta(days=365)).strftime('%Y-%m-%d')),
        (((start_dt + timedelta(days=365+1)).strftime('%Y-%m-%d')), to_str)
    ]
    
    all_data = []
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    
    with requests.Session() as s:
        def fetch_api(params):
            offset = 0
            params['limit'] = 5000
            while True:
                params['offset'] = offset
                try:
                    r = s.get(url, params=params, timeout=12)
                    if r.status_code == 200:
                        d = r.json().get('operationalData', [])
                        if not d: break
                        all_data.extend(d)
                        if len(d) < 5000: break
                        offset += 5000
                    else:
                        break
                except Exception:
                    break

        for d_start, d_end in date_ranges:
            for op in OPERATORS:
                fetch_api({
                    'indicator': 'Physical Flow', 
                    'from': d_start, 
                    'to': d_end,
                    'directionKey': 'entry', 
                    'operatorKey': op, 
                    'periodType': 'day'
                })

    df = pd.DataFrame(all_data)
    if df.empty:
        return pd.DataFrame()
        
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    df = df[df['periodType'].astype(str).str.lower() == 'day']
    df = df[df['directionKey'].astype(str).str.lower() == 'entry']

    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    df['Category'] = df.apply(get_baltic_category, axis=1)
    df = df.dropna(subset=['Category'])

    df_daily = df.groupby(['Date', 'Category', 'pointKey'], as_index=False)['value'].max()
    df_daily['Date_Parsed'] = pd.to_datetime(df_daily['Date'])
    df_daily['Month'] = df_daily['Date_Parsed'].dt.strftime('%Y-%m')

    monthly_summary = df_daily.groupby(['Month', 'Category'])['value'].sum().reset_index()
    monthly_summary['Value_TWh'] = monthly_summary['value'] / 1e9
    
    pivot = monthly_summary.pivot(index='Month', columns='Category', values='Value_TWh').fillna(0)
    return pivot


# --- 2. SUOMEN TARKAT KUUKAUSIVOLYYMIT (Inkoo & Hamina LNG) ---
@st.cache_data(ttl=3600)
def fetch_finland_actual_data():
    finland_data = {
        '2025-01': 0.3,
        '2025-02': 0.5,
        '2025-03': 0.4,
        '2025-04': 0.6,
        '2025-05': 1.7,
        '2025-06': 0.9,
        '2025-07': 2.3,
        '2025-08': 0.0,
        '2025-09': 0.0,
        '2025-10': 1.4,
        '2025-11': 0.8,
        '2025-12': 0.7,
        '2026-01': 0.5,
        '2026-02': 1.0,
        '2026-03': 0.9,
        '2026-04': 1.0,
        '2026-05': 1.4,
        '2026-06': 0.4,
        '2026-07': 0.7,
        '2026-08': 0.6
    }
    
    df_fi = pd.DataFrame(list(finland_data.items()), columns=['Month', 'Inkoo & Hamina LNG'])
    df_fi.set_index('Month', inplace=True)
    return df_fi


# --- 3. KÄYTTÖLIITTYMÄ JA YHDISTÄMINEN ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG** & **Gasgrid Finland actuals**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Refresh Data 🔄"):
    st.cache_data.clear()
    st.rerun()

df_baltic = fetch_baltic_data()
df_finland = fetch_finland_actual_data()

if df_baltic.empty:
    st.warning("Baltian dataa ei saatu ladattua.")
else:
    pivot_df = df_baltic.join(df_finland, how='outer').fillna(0)

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
        labels={'TWh': 'Energy (TWh / month)', 'Month': '
