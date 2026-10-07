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

# --- 1. ENTSOG DATA: OPTIMOITU TÄSMÄHAKU (4 KUTSUA) ---

@st.cache_data(ttl=86400, show_spinner="Noudetaan dataa ENTSOG-rajapinnasta (kestää n. 2-3 sekuntia)...")
def fetch_entsog_fast():
    today = datetime.today()
    # Otetaan noin 24 kuukauden historia
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    
    # 4 tarkkaa hakua kantaverkkoyhtiöiden tunnuksilla. 
    # Tämä ohittaa kokonaan LNG-terminaalien virheellisesti raportoimat maksimikapasiteetit.
    api_configs = [
        {'op': 'LV-TSO-0001', 'ind': 'Physical Flow'}, # Conexus (Latvia)
        {'op': 'LT-TSO-0001', 'ind': 'Physical Flow'}, # Amber Grid (Liettua)
        {'op': 'FI-TSO-0001', 'ind': 'Physical Flow'}, # Gasgrid (Suomi)
        {'op': 'FI-TSO-0001', 'ind': 'Allocation'}     # Gasgrid (Suomi - todellinen kaupallinen virta)
    ]
    
    all_data = []
    
    # Requests Session nopeuttaa toistuvia hakuja
    with requests.Session() as s:
        for cfg in api_configs:
            params = {
                'from': from_str,
                'to': to_str,
                'limit': 5000, # Koko historia mahtuu yhteen pyyntöön per operaattori
                'directionKey': 'entry',
                'periodType': 'day',
                'operatorKey': cfg['op'],
                'indicator': cfg['ind']
            }
            try:
                r = s.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    data = r.json().get('operationalData', [])
                    all_data.extend(data)
            except Exception:
                pass
                
    if not all_data:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_data)
    
    # --- DATAN PUHDISTUS ---
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    
    # Varmistetaan energiayksiköt
    if 'unit' in df.columns:
        df['unit_low'] = df['unit'].astype(str).str.lower()
        df = df[df['unit_low'].str.contains('kwh/d|kwh/h')]
        
        # Jos Suomi on ilmoittanut luvut tuntitehona (kWh/h), kerrotaan 24:llä
        hourly_mask = df['unit_low'].str.contains('kwh/h')
        df.loc[hourly_mask, 'value'] = df.loc[hourly_mask, 'value'] * 24

    # Poimitaan päivämäärä
    date_col = next((c for c in ['periodFrom', 'gasDayStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    
    # Luokittelufunktio
    def categorize(row):
        pk = str(row.get('pointKey', '')).upper()
        pl = str(row.get('pointLabel', '')).lower()
        if pk in ['ITP-00495', 'ITP-00508']:
            return 'Inkoo & Hamina LNG'
        if 'incukalns' in pl or 'inčukalns' in pl:
            return 'Inčukalns UGS (Withdrawal)'
        if 'klaip' in pl or 'independence' in pl or 'kn' in pl:
            if not ('gipl' in pl or 'santaka' in pl):
                return 'Klaipėda LNG'
        if 'gipl' in pl or 'santaka' in pl:
            return 'GIPL (Poland -> LT)'
        return None
        
    df['Category'] = df.apply(categorize, axis=1)
    df = df.dropna(subset=['Category'])
    
    # Duplikaattien karsinta
    # Jos Gasgrid raportoi samalle päivälle Allocation ja Physical Flow, suositaan Allocationia
    df['ind_rank'] = df['indicator'].astype(str).str.lower().map({'allocation': 1, 'physical flow': 2}).fillna(3)
    df = df.sort_values(['Category', 'pointKey', 'Date', 'ind_rank'])
    
    # Pidetään täsmälleen yksi totuus per piste ja päivä
    df_clean = df.drop_duplicates(subset=['Category', 'pointKey', 'Date'], keep='first')
    
    return df_clean


# --- 2. KÄYTTÖLIITTYMÄ (STREAMLIT UI) ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_entsog_fast()

if df_raw.empty:
    st.warning("Ei saatu yhteyttä ENTSOG API-rajapintaan tai data on tyhjä. Napsauta 'Clear Cache & Refresh'.")
else:
    df_filtered = df_raw.copy()
    df_filtered['Date_Parsed'] = pd.to_datetime(df_filtered['Date'], utc=True)
    df_filtered['Month'] = df_filtered['Date_Parsed'].dt.strftime('%Y-%m')

    # Aggregointi kuukausitasolle (kWh -> TWh muunnos: / 1e9)
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
