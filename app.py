import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import time

# Sovelluksen sivun asetukset
st.set_page_config(
    page_title="FinBalt Natural Gas Entry Flows",
    page_icon="🔥",
    layout="wide"
)

# --- BALTIAN HAKU (TÄYSIN KOSKEMATON JA ENNALLAAN) ---
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_entsog_data_optimized(start_date_str, end_date_str):
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    
    all_data = []
    current_start = start_dt
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_days = (end_dt - start_dt).days or 1
    
    while current_start < end_dt:
        current_end = min(current_start + timedelta(days=14), end_dt)
        from_str = current_start.strftime('%Y-%m-%d')
        to_str = current_end.strftime('%Y-%m-%d')
        
        elapsed_days = (current_start - start_dt).days
        progress = min(elapsed_days / total_days, 1.0)
        progress_bar.progress(progress)
        status_text.text(f"Fetching data from ENTSOG API: {from_str} to {to_str}...")
        
        offset = 0
        limit = 5000
        
        while True:
            params = {
                'indicator': 'Physical Flow',
                'from': from_str,
                'to': to_str,
                'limit': limit,
                'offset': offset
            }
            
            max_retries = 3
            success = False
            
            for attempt in range(1, max_retries + 1):
                try:
                    response = requests.get(url, params=params, timeout=25)
                    
                    if response.status_code == 200:
                        data = response.json().get('operationalData', [])
                        if data:
                            all_data.extend(data)
                            if len(data) >= limit:
                                offset += limit
                            else:
                                success = True
                                break
                        else:
                            success = True
                            break
                    elif response.status_code == 404:
                        success = True
                        break
                    else:
                        time.sleep(attempt)
                except (requests.exceptions.Timeout, requests.exceptions.RequestException):
                    time.sleep(attempt)
            
            if not success or (response.status_code == 200 and len(data) < limit) or response.status_code == 404:
                break
                
        current_start = current_end + timedelta(days=1)
        
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(all_data)


# --- SUOMEN ERILLISHAKU (Inkoo ITP-00495 & Hamina ITP-00508) ---
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_finland_lng_data(start_date_str, end_date_str):
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    finland_data = []
    
    # Haetaan Inkoo ja Hamina pisteittäin
    points = ['ITP-00495', 'ITP-00508']
    with requests.Session() as s:
        for pk in points:
            for ind in ['Physical Flow', 'Allocation']:
                params = {
                    'indicator': ind,
                    'from': start_date_str,
                    'to': end_date_str,
                    'pointKey': pk,
                    'directionKey': 'entry',
                    'periodType': 'day',
                    'limit': 5000
                }
                try:
                    r = s.get(url, params=params, timeout=15)
                    if r.status_code == 200:
                        d = r.json().get('operationalData', [])
                        if d:
                            finland_data.extend(d)
                except Exception:
                    pass
    return pd.DataFrame(finland_data)


def classify_flow(row):
    point_label = str(row.get('pointLabel', '')).lower()
    operator_label = str(row.get('operatorLabel', '')).lower()
    pk = str(row.get('pointKey', '')).upper()
    direction = str(row.get('directionKey', '')).lower()
    
    if direction != 'entry':
        return None
        
    if pk in ['ITP-00495', 'ITP-00508'] or 'inkoo' in point_label or 'hamina' in point_label:
        return 'Inkoo & Hamina LNG'
    elif 'incukalns' in point_label or 'inčukalns' in point_label:
        return 'Inčukalns UGS (Withdrawal)'
    elif 'gipl' in point_label or 'santaka' in point_label:
        return 'GIPL (Poland -> LT)'
    elif 'klaipeda' in point_label or 'klaipėda' in point_label:
        if 'lng' in point_label or 'terminal' in point_label or 'amber' in operator_label or 'kn' in operator_label:
            return 'Klaipėda LNG'
            
    return None


# --- UI / Streamlit App ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_fetch = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=6, step=1)

if st.sidebar.button("Refresh Data 🔄"):
    st.cache_data.clear()
    st.rerun()

today = datetime.today()
first_day_current_month = today.replace(day=1)
start_dt = (first_day_current_month - timedelta(days=months_to_fetch * 31)).replace(day=1)

start_date = start_dt.strftime('%Y-%m-%d')
end_date = today.strftime('%Y-%m-%d')

# 1. Haetaan Baltia (alkuperäinen koodi)
df_baltic = fetch_entsog_data_optimized(start_date, end_date)
# 2. Haetaan Suomi erikseen luotettavasti
df_finland = fetch_finland_lng_data(start_date, end_date)

# Yhdistetään datat
frames = [df for df in [df_baltic, df_finland] if df is not None and not df.empty]
if not frames:
    df_raw = pd.DataFrame()
else:
    df_raw = pd.concat(frames, ignore_index=True)

if df_raw.empty:
    st.warning("No data retrieved from ENTSOG. Please try clicking 'Refresh Data' or reduce the month range.")
else:
    df_raw['Category'] = df_raw.apply(classify_flow, axis=1)
    df_filtered = df_raw.dropna(subset=['Category']).copy()
    
    if df_filtered.empty:
        st.warning("No matching entry flows found for the selected time period.")
    else:
        date_candidates = ['periodFrom', 'gasDayStart', 'periodStart', 'gasDayStartedOn']
        date_col = next((c for c in date_candidates if c in df_filtered.columns), None)
        if not date_col:
            date_col = next((c for c in df_filtered.columns if 'period' in c.lower() or 'date' in c.lower()), None)

        df_filtered['value'] = pd.to_numeric(df_filtered['value'], errors='coerce').fillna(0)
        
        # --- TÄRKEÄ KORJAUS SUOMELLE: YKSIKKÖSKAALAUS (kWh/h -> kWh/d) ---
        if 'unit' in df_filtered.columns:
            unit_low = df_filtered['unit'].astype(str).str.lower()
            # Jos Suomen piste ilmoitetaan tuntitehona, kerrotaan 24:llä
            is_finland_hourly = (df_filtered['Category'] == 'Inkoo & Hamina LNG') & unit_low.str.contains('kwh/h')
            df_filtered.loc[is_finland_hourly, 'value'] = df_filtered.loc[is_finland_hourly, 'value'] * 24

        df_filtered['Date_Parsed'] = pd.to_datetime(df_filtered[date_col], utc=True)
        df_filtered['Date_Only'] = df_filtered['Date_Parsed'].dt.date
        
        # --- KAPASITEETTILEIKKURI SUOMELLE ---
        def clean_flows(row):
            if row['Category'] != 'Inkoo & Hamina LNG':
                return row['value']
            v = row['value']
            # Poistetaan ENTSOGin syöttämät kapasiteettihaamut (> 35 GWh päivässä)
            if v > 35000000:
                return 0.0
            return v

        df_filtered['clean_value'] = df_filtered.apply(clean_flows, axis=1)

        # --- DUPLIKAATTIEN POISTO PÄIVÄTASOLLA ---
        df_daily = df_filtered.groupby(['Date_Only', 'Category', 'pointKey'], as_index=False)['clean_value'].max()
        df_daily.rename(columns={'clean_value': 'value'}, inplace=True)
        
        df_daily['Date_Parsed'] = pd.to_datetime(df_daily['Date_Only'])
        df_daily['Month'] = df_daily['Date_Parsed'].dt.strftime('%Y-%m')
        
        # --- KUUKAUSISUMMAUS ---
        monthly_summary = df_daily.groupby(['Month', 'Category'])['value'].sum().reset_index()
        monthly_summary['Value_TWh'] = monthly_summary['value'] / 1e9
        
        pivot_df = monthly_summary.pivot(index='Month', columns='Category', values='Value_TWh').fillna(0)
        
        categories_order = [
            'Inčukalns UGS (Withdrawal)', 
            'GIPL (Poland -> LT)', 
            'Klaipėda LNG', 
            'Inkoo & Hamina LNG'
        ]
        existing_cols = [col for col in categories_order if col in pivot_df.columns]
        pivot_df = pivot_df[existing_cols].tail(months_to_fetch)
        
        latest_month = pivot_df.index[-1]
        latest_total = pivot_df.loc[latest_month].sum()
        
        # --- Overview Metrics ---
        st.subheader(f"Latest Month Overview ({latest_month})")
        m_cols = st.columns(len(existing_cols) + 1)
        
        m_cols[0].metric(label="Total Supply", value=f"{latest_total:.3f} TWh")
        for idx, col in enumerate(existing_cols):
            val = pivot_df.loc[latest_month, col]
            m_cols[idx + 1].metric(label=col, value=f"{val:.3f} TWh")
            
        st.markdown("---")
        
        # --- Plotly Chart ---
        st.subheader("Monthly Gas Supply by Route (TWh)")
        
        plot_df = pivot_df.reset_index().melt(id_vars='Month', var_name='Entry Route', value_name='TWh')
        
        fig = px.bar(
            plot_df, 
            x='Month', 
            y='TWh', 
            color='Entry Route',
            title=f"FinBalt Natural Gas Entry Flows (Last {months_to_fetch} Months)",
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
        
        # --- Data Table ---
        st.subheader("Data Summary Table")
        display_df = pivot_df.copy()
        display_df['Total (TWh)'] = display_df.sum(axis=1)
        
        st.dataframe(display_df.style.format("{:.3f}"), use_container_width=True)
        
        csv_data = display_df.to_csv().encode('utf-8')
        st.download_button(
            label="Download Data as CSV 📥",
            data=csv_data,
            file_name=f"finbalt_gas_entry_flows_{latest_month}.csv",
            mime="text/csv"
        )
