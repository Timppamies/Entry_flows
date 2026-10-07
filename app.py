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

# --- 1. ENTSOG DATA: SALAMAHAKU JA ÄLYKÄS SUODATUS ---

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

@st.cache_data(ttl=86400, show_spinner="Noudetaan FinBalt gas entry -historiaa (kestää 2-4 sekuntia)...")
def fetch_full_entsog_entry_history():
    today = datetime.today()
    start_dt = (today.replace(day=1) - timedelta(days=24 * 31)).replace(day=1)
    
    from_str = start_dt.strftime('%Y-%m-%d')
    to_str = today.strftime('%Y-%m-%d')
    
    all_data = []
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    
    # Käytetään Sessionia API-kutsujen nopeuttamiseksi
    with requests.Session() as s:
        def fetch(params):
            offset = 0
            params['limit'] = 5000
            while True:
                params['offset'] = offset
                try:
                    resp = s.get(url, params=params, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json().get('operationalData', [])
                        if not data: break
                        all_data.extend(data)
                        if len(data) < 5000: break
                        offset += 5000
                    else:
                        break
                except Exception:
                    break

        # 1. Baltia (Conexus & Amber Grid) - Vain 2 kutsua
        for op in ['LV-TSO-0001', 'LT-TSO-0001']:
            fetch({
                'indicator': 'Physical Flow', 'from': from_str, 'to': to_str,
                'directionKey': 'entry', 'operatorKey': op, 'periodType': 'day'
            })
        
        # 2. Suomi (Inkoo & Hamina) - Vain 1 yhdistetty kutsu
        # Haemme vain Physical Flow, sillä Allocation sisältää haamunollia.
        fetch({
            'indicator': 'Physical Flow', 'from': from_str, 'to': to_str,
            'directionKey': 'entry', 'pointKey': 'ITP-00495,ITP-00508', 'periodType': 'day'
        })

    df = pd.DataFrame(all_data)
    if df.empty:
        return df

    # Varmistetaan luvut
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0)
    
    # Yksikkökorjaus (Jos Suomi raportoi kWh/h, skaalataan se päivävirraksi)
    if 'unit' in df.columns:
        df['unit_low'] = df['unit'].astype(str).str.lower()
        hourly_mask = df['unit_low'].str.contains('kwh/h')
        df.loc[hourly_mask, 'value'] = df.loc[hourly_mask, 'value'] * 24

    # Perussuodatukset
    if 'periodType' in df.columns:
        df = df[df['periodType'].astype(str).str.lower() == 'day']
    if 'directionKey' in df.columns:
        df = df[df['directionKey'].astype(str).str.lower() == 'entry']

    # Poimitaan päivämäärä
    date_col = next((c for c in ['periodFrom', 'gasDayStart', 'periodStart'] if c in df.columns), 'periodFrom')
    df['Date'] = pd.to_datetime(df[date_col], utc=True).dt.date
    
    # Luokitellaan maantieteellisesti
    df['Category'] = df.apply(get_category, axis=1)
    df = df.dropna(subset=['Category'])

    # --- LOPULLINEN RATKAISU: ÄLYKÄS DATA-SPLIT ---
    
    # 1. Suomen luvut (Inkoo & Hamina)
    # Suomen rajapinta palauttaa samaan aikaan todellisen virran (esim. 16 GWh tai 0 GWh) JA maksimikapasiteetin (140 GWh).
    # Ottamalla MINIMIN, kapasiteettihuijaus ohitetaan täydellisesti ja käteen jää aito virtaus!
    df_fin = df[df['Category'] == 'Inkoo & Hamina LNG'].groupby(['Category', 'pointKey', 'Date'], as_index=False)['value'].min()
    
    # 2. Baltian luvut (Klaipeda, GIPL, Incukalns)
    # Baltia raportoi datansa puhtaasti. Ottamalla MAKSIMIN vältämme mahdolliset 'haamunollat', joita heidän järjestelmänsä joskus syöttää.
    df_balt = df[df['Category'] != 'Inkoo & Hamina LNG'].groupby(['Category', 'pointKey', 'Date'], as_index=False)['value'].max()
    
    # Yhdistetään puhtaat datat
    df_clean = pd.concat([df_fin, df_balt], ignore_index=True)

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
