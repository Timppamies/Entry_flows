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

# --- 1. ENTSOG DATA: NOPEA HAKU JA VEKTOROITU KAPASITEETTILEIKKURI ---

OPERATORS = [
    'LV-TSO-0001', # Conexus Baltic Grid
    'LT-TSO-0001', # Amber Grid
]

FINLAND_POINTS = [
    'ITP-00495', # Inkoo FSRU
    'ITP-00508', # Hamina LNG
]

def get_category(row):
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

@st.cache_data(ttl=86400, show_spinner="Noudetaan ja puhdistetaan dataa (kestää n. 2-5 sekuntia)...")
def fetch_and_clean_entsog_data():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    # Jaetaan varalta kahteen hakuun, jotta API ei pätki
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
            # 1. Baltia
            for op in OPERATORS:
                fetch_api({
                    'indicator': 'Physical Flow', 'from': d_start, 'to': d_end,
                    'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
                })
                
            # 2. Suomi
            fetch_api({
                'indicator': 'Physical Flow', 'from': d_start, 'to': d_end,
                'directionKey': 'entry', 'pointKey': 'ITP-00495,ITP-00508', 'periodType': 'day'
            })

    df = pd.DataFrame(all_data)
    if df.empty:
        return df

    # Perussiivous
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    df = df[df['periodType'].astype(str).str.lower() == 'day']
    df = df[df['directionKey'].astype(str).str.lower() == 'entry']

    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- KAPASITEETTILEIKKURI (Pandas-turvallinen vektoriversio) ---
    # Tämä estää KeyErrorin ja tuhoaa ENTSOGin syöttämät maksimikapasiteetit lennosta.
    def cap_cutter(row):
        pk = str(row['pointKey']).upper()
        v = row['value']
        # Jos Inkoon lukema on yli 130 GWh, se on satavarmasti maksimikapasiteetti, muutetaan nollaksi.
        if pk == 'ITP-00495' and v > 130000000:
            return 0.0
        # Jos Haminan lukema on yli 35 GWh, se on satavarmasti maksimikapasiteetti, muutetaan nollaksi.
        if pk == 'ITP-00508' and v > 35000000:
            return 0.0
        return v
        
    df['clean_value'] = df.apply(cap_cutter, axis=1)
    
    # Nyt kun kapasiteetit on ammuttu nollaksi, voimme turvallisesti ottaa päivän maksimin.
    # Jos Inkoosta palasi kapasiteetti (nyt 0) ja todellinen virta (esim. 16M), max on 16M.
    # Jos terminaali on kiinni, kaikki rivit ovat nollia, jolloin max on 0.
    df_clean = df.groupby(['Category', 'pointKey', 'Date'], as_index=False)['clean_value'].max()
    
    # Palautetaan sarakkeen nimeksi takaisin 'value' UI:ta varten
    df_clean = df_clean.rename(columns={'clean_value': 'value'})

    return df_clean


# --- 2. KÄYTTÖLIITTYMÄ (STREAMLIT UI) ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_and_clean_entsog_data()

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
