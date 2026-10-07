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

# --- 1. ENTSOG DATA: OPTIMOITU SALAMAHAKU ---

OPERATORS = [
    'LV-TSO-0001', # Conexus Baltic Grid (Inčukalns)
    'LT-TSO-0001', # Amber Grid (Klaipėda, GIPL)
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

@st.cache_data(ttl=86400, show_spinner="Ladataan FinBalt gas entry -historiaa (noin 2-5 sekuntia)...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    # Lasketaan dynaamisesti 24 kk taaksepäin nykyisestä kuukaudesta
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')

    all_data = []
    
    # Käytetään Sessionia. Se pitää yhteyden auki API:in, jolloin haku on moninkertaisesti nopeampi.
    session = requests.Session()
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"

    # 1. Baltian TSO:t (Haetaan suoraan Physical Flow)
    for op in OPERATORS:
        params = {
            'indicator': 'Physical Flow',
            'from': from_str, 'to': to_str, 'limit': 5000,
            'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
        }
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                all_data.extend(resp.json().get('operationalData', []))
        except:
            pass

    # 2. Suomen terminaalit (Haetaan yhdessä nipussa, ENTSOG palauttaa tällöin kaikki indikaattorit)
    for pk in FINLAND_POINTS:
        params = {
            'from': from_str, 'to': to_str, 'limit': 5000,
            'directionKey': 'entry', 'pointKey': pk, 'periodType': 'day'
        }
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                all_data.extend(resp.json().get('operationalData', []))
        except:
            pass

    df = pd.DataFrame(all_data)
    if df.empty:
        return df

    # Varmistetaan luvut
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    
    # Suodatetaan roskat
    if 'periodType' in df.columns:
        df = df[df['periodType'].astype(str).str.lower() == 'day']
    if 'directionKey' in df.columns:
        df = df[df['directionKey'].astype(str).str.lower() == 'entry']
        
    # Sallitaan sekä fyysinen virta että allokaatio, koska LNG-terminaalit käyttävät kumpaakin sekaisin
    if 'indicator' in df.columns:
        valid_inds = ['physical flow', 'allocation']
        df = df[df['indicator'].astype(str).str.lower().isin(valid_inds)]

    # Poimitaan päivämäärä
    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    
    # Luokitellaan maantieteellisesti
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- LOPULLINEN RATKAISU DUPLIKAATTEIHIN JA HAAMULUKUIHIN ---
    # Ryhmitellään tulokset pisteen ja päivän mukaan ja poimitaan PIENIN arvo.
    # Koska ENTSOG palauttaa päällekkäin esim. todellisen virran (0 kWh tai 16 000 000 kWh) 
    # sekä terminaalin maksimikapasiteetin (140 000 000 kWh),
    # minimin ottaminen nappaa matemaattisen satavarmasti oikean virran ja jättää kapasiteettivuoren huomiotta.
    df_clean = df.groupby(['Category', 'pointKey', 'Date'], as_index=False)['value'].min()

    return df_clean


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
