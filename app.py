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

# --- 1. ENTSOG DATA: UUSI HAKULOGIIKKA ---

# Baltian operaattorit (Nämä toimivat oikein päivätasolla)
OPERATORS_BALTIC = ['LV-TSO-0001', 'LT-TSO-0001']

# Suomen pisteet (Näillä päivädata on saastunut kapasiteetilla)
FINLAND_POINTS = ['ITP-00495', 'ITP-00508']

def get_category(row):
    pk = str(row.get('pointKey', '')).upper()
    pl = str(row.get('pointLabel', '')).lower()
    if pk in FINLAND_POINTS: return 'Inkoo & Hamina LNG'
    if 'incukalns' in pl or 'inčukalns' in pl: return 'Inčukalns UGS (Withdrawal)'
    if 'klaip' in pl or 'independence' in pl or 'kn' in pl:
        if not ('gipl' in pl or 'santaka' in pl): return 'Klaipėda LNG'
    if 'gipl' in pl or 'santaka' in pl: return 'GIPL (Poland -> LT)'
    return None

@st.cache_data(ttl=86400, show_spinner="Koodi uusittu: Haetaan Suomen data puhtaalta tuntitasolta (ohittaa bugin)...")
def fetch_entsog_data_from_scratch():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    session = requests.Session()

    def fetch_api(params):
        results = []
        offset = 0
        params['limit'] = 5000
        while True:
            params['offset'] = offset
            try:
                r = session.get(url, params=params, timeout=12)
                if r.status_code == 200:
                    d = r.json().get('operationalData', [])
                    if not d: break
                    results.extend(d)
                    if len(d) < 5000: break
                    offset += 5000
                else:
                    break
            except Exception:
                break
        return results

    # 1. HAE BALTIA (Päivätasolla, puhdas data)
    balt_data = []
    for op in OPERATORS_BALTIC:
        balt_data.extend(fetch_api({
            'indicator': 'Physical Flow', 'from': from_str, 'to': to_str,
            'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
        }))
        
    df_balt = pd.DataFrame(balt_data)
    if not df_balt.empty:
        df_balt['value'] = pd.to_numeric(df_balt['value'], errors='coerce').fillna(0)
        df_balt = df_balt[df_balt['directionKey'].astype(str).str.lower() == 'entry']
        date_col = next((c for c in ['periodFrom', 'gasDayStart'] if c in df_balt.columns), 'periodFrom')
        df_balt['Date'] = pd.to_datetime(df_balt[date_col], utc=True).dt.date
        
        # Puhdistetaan duplikaatit
        df_balt = df_balt.groupby(['pointKey', 'pointLabel', 'Date'], as_index=False)['value'].max()
        df_balt['Category'] = df_balt.apply(get_category, axis=1)

    # 2. HAE SUOMI (Uusi logiikka: Tuntitasolla!)
    # Koska periodType=day on täysin korruptoitunut (palauttaa jatkuvasti kapasiteettia), 
    # haemme aidon tuntikohtaisen (kWh/h) virtauksen. 
    fin_data = []
    for pk in FINLAND_POINTS:
        fin_data.extend(fetch_api({
            'indicator': 'Physical Flow', 'from': from_str, 'to': to_str,
            'directionKey': 'entry', 'pointKey': pk, 'periodType': 'hour'
        }))
        
    df_fin = pd.DataFrame(fin_data)
    if not df_fin.empty:
        df_fin['value'] = pd.to_numeric(df_fin['value'], errors='coerce').fillna(0)
        df_fin = df_fin[df_fin['directionKey'].astype(str).str.lower() == 'entry']
        
        # Otetaan ylös tarkka tunti sekä päivämäärä
        df_fin['Exact_Hour'] = pd.to_datetime(df_fin['periodFrom'], utc=True)
        df_fin['Date'] = df_fin['Exact_Hour'].dt.date
        
        # Vaihe A: Karsitaan tuntitason duplikaatit (otetaan varalta maksimi per tunti)
        df_fin_hours = df_fin.groupby(['pointKey', 'pointLabel', 'Date', 'Exact_Hour'], as_index=False)['value'].max()
        
        # Vaihe B: Summataan vuorokauden tunnit yhteen -> Saadaan aito päivävirtaus!
        # Koska yksi tunti teholla 1 kWh/h = 1 kWh, tuntien summaus antaa suoraan vuorokauden energian (kWh/d).
        df_fin = df_fin_hours.groupby(['pointKey', 'pointLabel', 'Date'], as_index=False)['value'].sum()
        df_fin['Category'] = df_fin.apply(get_category, axis=1)

    # Yhdistetään datat
    frames = []
    if not df_balt.empty: frames.append(df_balt)
    if not df_fin.empty: frames.append(df_fin)
    
    if not frames:
        df_final = pd.DataFrame()
    else:
        df_final = pd.concat(frames, ignore_index=True)
        df_final = df_final.dropna(subset=['Category'])
    
    return df_final


# --- 2. KÄYTTÖLIITTYMÄ (STREAMLIT UI) ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_show = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=12, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

df_raw = fetch_entsog_data_from_scratch()

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
