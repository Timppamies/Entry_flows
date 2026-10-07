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

# --- 1. ENTSOG DATA: OPTIMOITU JA ÄLYKÄS HAKU ---

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

@st.cache_data(ttl=86400, show_spinner="Haetaan ja puhdistetaan rajapintadataa (kestää noin 5-10 sekuntia)...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    # Jaetaan 2 vuoden haku kahteen palaan, jotta API ei pätki
    date_ranges = [
        (from_str, (start_dt + timedelta(days=365)).strftime('%Y-%m-%d')),
        (((start_dt + timedelta(days=365+1)).strftime('%Y-%m-%d')), to_str)
    ]

    all_data = []
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    session = requests.Session()

    def fetch(params):
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get('operationalData', [])
                if data:
                    all_data.extend(data)
        except Exception:
            pass

    for d_start, d_end in date_ranges:
        # 1. Baltia: Haetaan suoraan operaattorilla ja Physical Flow'lla (toimii heille luotettavasti)
        for op in OPERATORS:
            fetch({
                'indicator': 'Physical Flow', 'from': d_start, 'to': d_end,
                'limit': 5000, 'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
            })
        
        # 2. Suomi: Haetaan pisteillä. Haetaan SEKÄ Allocation ETTÄ Physical Flow.
        # Allocation tuo oikeat kaupalliset virrat.
        for pk in FINLAND_POINTS:
            for ind in ['Allocation', 'Physical Flow', 'Nomination']:
                fetch({
                    'indicator': ind, 'from': d_start, 'to': d_end,
                    'limit': 5000, 'directionKey': 'entry', 'pointKey': pk, 'periodType': 'day'
                })

    df = pd.DataFrame(all_data)
    if df.empty:
        return df

    # Varmistetaan sarakkeet
    for col in ['indicator', 'statusKey']:
        if col not in df.columns:
            df[col] = ''

    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    df = df[df['periodType'].astype(str).str.lower() == 'day']
    df = df[df['directionKey'].astype(str).str.lower() == 'entry']

    # Pudotetaan välittömästi kaikki kapasiteettiin viittaavat indikaattorit (jos niitä eksyi mukaan)
    df = df[~df['indicator'].astype(str).str.lower().str.contains('capacity')]

    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- ÄLYKÄS TUOMARI: LOPULLINEN DUPLIKAATTIEN PURKU ---
    
    # 1. Arvostellaan Indikaattorit (1 on paras)
    # Allocation (allokoitu toteuma) on aina oikea kaasuvirta. 
    # Physical Flow luokitellaan huonoimmaksi Suomelle, koska se on saastunut kapasiteetilla.
    def get_ind_rank(ind):
        ind = str(ind).lower()
        if 'allocation' in ind: return 1
        if 'measured' in ind: return 2
        if 'nomination' in ind: return 3
        if 'physical flow' in ind: return 4 
        return 5
    df['ind_rank'] = df['indicator'].apply(get_ind_rank)
    
    # 2. Arvostellaan Status (1 on paras)
    # Lopullinen toteutuma (Actual) on luotettavampi kuin alustava ilmoitus
    def get_stat_rank(stat):
        stat = str(stat).lower()
        if 'actual' in stat: return 1
        if 'clearance' in stat: return 2
        if 'confirmed' in stat: return 3
        return 4
    df['stat_rank'] = df['statusKey'].apply(get_stat_rank)
    
    # 3. Järjestetään data: 
    # - Ensin paras indikaattori (Allocation)
    # - Sitten paras status (Actual)
    # - LOPUKSI SUURIN ARVO (value=False). Tämä estää ohjelmaa ottamasta nollaa, jos päivältä löytyy myös todellinen virtaus!
    df = df.sort_values(
        by=['Category', 'pointKey', 'Date', 'ind_rank', 'stat_rank', 'value'],
        ascending=[True, True, True, True, True, False]
    )
    
    # 4. Pudotetaan duplikaatit pitämällä ensimmäinen rivi. Takaa täydellisen tarkkuuden per päivä.
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

df_raw = fetch_full_entsog_entry_history()

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
