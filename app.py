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

# --- 1. ENTSOG DATA ---

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

@st.cache_data(ttl=86400, show_spinner="Ladataan FinBalt gas entry -historiaa (optimoitu salamahaku)...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    # ENTSOGin rivirajoitus (5000) riittää kirkkaasti yhdelle pisteelle 2 vuoden hakuun (730 riviä).
    # Ei enää pätkitä päivämääriä, jolloin API-kutsujen määrä putoaa minimiin!
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')

    all_data = []
    
    # Käytetään Sessionia, joka pitää yhteyden auki ja nopeuttaa hakuja merkittävästi
    session = requests.Session()
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"

    def fetch_data(params):
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json().get('operationalData', [])
        except Exception:
            pass
        return []

    # 1. Baltia (Pelkkä Physical Flow riittää)
    for op in OPERATORS:
        all_data.extend(fetch_data({
            'indicator': 'Physical Flow',
            'from': from_str, 'to': to_str, 'limit': 5000,
            'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
        }))

    # 2. Suomi (Haetaan Allocation JA Physical Flow erikseen)
    for pk in FINLAND_POINTS:
        for ind in ['Allocation', 'Physical Flow']:
            all_data.extend(fetch_data({
                'indicator': ind,
                'from': from_str, 'to': to_str, 'limit': 5000,
                'directionKey': 'entry', 'pointKey': pk, 'periodType': 'day'
            }))

    df = pd.DataFrame(all_data)
    if df.empty:
        return df

    # Varmistetaan luvut
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    
    # Perussuodatukset
    if 'periodType' in df.columns:
        df = df[df['periodType'].astype(str).str.lower() == 'day']
    if 'directionKey' in df.columns:
        df = df[df['directionKey'].astype(str).str.lower() == 'entry']
        
    # HUOM: Sallitaan nyt vihdoin molemmat indikaattorit (Tässä oli aiemman koodin virhe!)
    if 'indicator' in df.columns:
        valid_inds = ['physical flow', 'allocation']
        df = df[df['indicator'].astype(str).str.lower().isin(valid_inds)]

    # Poimitaan päivämäärä
    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    
    # Luokitellaan maantieteellisesti
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- ÄLYKÄS DUPLIKAATTIEN PURKU (Ohittaa 140 GWh kapasiteettivirheet) ---
    def pick_best_flow(group):
        # 1. Ensisijaisesti 'Allocation' (Tämä on todellinen virta, n. 16 GWh/päivä)
        alloc = group[group['indicator'].astype(str).str.lower() == 'allocation']
        if not alloc.empty:
            pos = alloc[alloc['value'] > 0]
            # Valitaan pienin nollasta poikkeava (tai 0 jos ei ollut virtaa)
            return pos.loc[pos['value'].idxmin()] if not pos.empty else alloc.iloc[0]
            
        # 2. Jos Allokaatiota ei jostain syystä ole, käytetään 'Physical Flow'
        phys = group[group['indicator'].astype(str).str.lower() == 'physical flow']
        if not phys.empty:
            pos = phys[phys['value'] > 0]
            # Ottamalla pienimmän ohitamme 140 GWh "Firm Technical Capacity" -harhan
            return pos.loc[pos['value'].idxmin()] if not pos.empty else phys.iloc[0]
            
        return group.iloc[0]

    # Ajetaan suodatus pisteen ja päivän mukaan
    df_clean = df.groupby(['Category', 'pointKey', 'Date'], as_index=False).apply(pick_best_flow).reset_index(drop=True)

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
