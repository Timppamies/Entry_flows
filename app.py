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

# --- 1. ENTSOG DATA: NOPEA HAKU VÄLIMUISTILLA ---

def get_category(row):
    pk = str(row.get('pointKey', '')).upper()
    pl = str(row.get('pointLabel', '')).lower()
    
    if pk in ['ITP-00495', 'ITP-00508']: return 'Inkoo & Hamina LNG'
    if 'incukalns' in pl or 'inčukalns' in pl: return 'Inčukalns UGS (Withdrawal)'
    if 'klaip' in pl or 'independence' in pl or 'kn' in pl:
        if not ('gipl' in pl or 'santaka' in pl): return 'Klaipėda LNG'
    if 'gipl' in pl or 'santaka' in pl: return 'GIPL (Poland -> LT)'
    return None

@st.cache_data(ttl=3600, show_spinner="Noudetaan dataa ENTSOG-rajapinnasta (kestää noin 10-15 sekuntia)...")
def fetch_fast_entsog_data():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    # Jaetaan kysely kahteen osaan API-vakauden varmistamiseksi
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
                    r = s.get(url, params=params, timeout=15)
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
            # 1. Baltia (Pelkkä Physical Flow on heille luotettava)
            for op in ['LV-TSO-0001', 'LT-TSO-0001']:
                fetch_api({
                    'indicator': 'Physical Flow', 'from': d_start, 'to': d_end,
                    'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
                })
            # 2. Suomi (Haetaan Allocation JA Physical Flow, jotta mikään aito virtauspäivä ei katoa)
            for ind in ['Physical Flow', 'Allocation']:
                fetch_api({
                    'indicator': ind, 'from': d_start, 'to': d_end,
                    'directionKey': 'entry', 'pointKey': 'ITP-00495,ITP-00508', 'periodType': 'day'
                })

    df = pd.DataFrame(all_data)
    return df


# --- 2. KÄYTTÖLIITTYMÄ JA DATAN PUHDISTUS ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Refresh Data 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_fast_entsog_data()

if df_raw.empty:
    st.warning("Ei saatu yhteyttä ENTSOG API-rajapintaan tai data on tyhjä.")
else:
    df = df_raw.copy()
    
    # Varmistetaan luvut
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    df = df[df['periodType'].astype(str).str.lower() == 'day']
    df = df[df['directionKey'].astype(str).str.lower() == 'entry']

    # Kategoriat ja päivämäärät
    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- TÄRKEIN LOGIIKKA: KAPASITEETTILEIKKURI (Bugin tuhoaja) ---
    is_inkoo = (df['pointKey'].astype(str).str.upper() == 'ITP-00495')
    is_hamina = (df['pointKey'].astype(str).str.upper() == 'ITP-00508')
    
    # Tunnistetaan tekniset maksimikapasiteetit. 
    # Inkoon kapasiteetti on n. 140 GWh -> Leikataan kaikki > 100 GWh
    # Haminan kapasiteetti on n. 40 GWh -> Leikataan kaikki > 30 GWh
    mask_cap_inkoo = is_inkoo & (df['value'] >= 100000000)
    mask_cap_hamina = is_hamina & (df['value'] >= 30000000)
    
    # TIPUTETAAN valtavat kapasiteettirivit KOKONAAN pois DataFrame:sta!
    df_real = df[~(mask_cap_inkoo | mask_cap_hamina)].copy()
    
    # --- PÄIVÄTASON AGGREGOINTI ---
    # Nyt kun kapasiteetit on tuhottu, jäljellä on vain oikeita virtauksia (esim. 16 milj. kWh ja nollia).
    # Ryhmitellään piste ja päivä, ja otetaan näistä MAKSIMI.
    # Tämä yhdistää Allocationin ja Physical Flow'n tiedot oikein siten, että todellinen virta poimitaan aina!
    df_daily = df_real.groupby(['Date', 'Category', 'pointKey'], as_index=False)['value'].max()
    
    # --- KUUKAUSITASON AGGREGOINTI ---
    df_daily['Date_Parsed'] = pd.to_datetime(df_daily['Date'])
    df_daily['Month'] = df_daily['Date_Parsed'].dt.strftime('%Y-%m')

    # Nyt koodi summaa kuukaudelta VAIN nuo puhdistetut 16 GWh päivävirrat, jolloin tulos asettuu odotettuun 0.5 - 1.0 TWh -mittakaavaan!
    monthly_summary = df_daily.groupby(['Month', 'Category'])['value'].sum().reset_index()
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

    # --- KÄYTTÖLIITTYMÄN PIIRTÄMINEN ---
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
