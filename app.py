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

# Relevantit ENTSOG-pisteet (Inkoo, Hamina, Klaipėda, Santaka/GIPL, Inčukalns)
# Suodatus suoraan API-tasolla estää muistin ylittymisen
TARGET_POINTS = [
    'FI-TP-0001', 'FI-TP-0002', # Inkoo / Hamina LNG
    'LT-TP-0001', 'LT-TP-0002', # Klaipeda LNG / GIPL Santaka
    'LV-TP-0001'                # Incukalns UGS
]

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_entsog_chunk(from_str, to_str):
    """
    Välimuistitetaan yksittäiset 14 päivän jaksot erikseen. 
    Näin sliderin siirtäminen ei tee koko haku uudestaan, 
    vaan hyödyntää aiemmin ladatut pätkät.
    """
    url = "https://transparency.entsog.eu/api/v1/operationalData.json"
    offset = 0
    limit = 5000
    chunk_data = []
    
    while True:
        params = {
            'indicator': 'Physical Flow',
            'from': from_str,
            'to': to_str,
            'limit': limit,
            'offset': offset,
            'directionKey': 'entry' # Haetaan vain entry-virrat
        }
        
        try:
            response = requests.get(url, params=params, timeout=20)
            if response.status_code == 200:
                data = response.json().get('operationalData', [])
                if not data:
                    break
                chunk_data.extend(data)
                if len(data) < limit:
                    break
                offset += limit
            elif response.status_code == 404:
                break
            else:
                time.sleep(1)
                break
        except Exception:
            break
            
    return chunk_data

def get_all_entsog_data(start_date_str, end_date_str):
    """
    Kokoaa haun hyödyntäen välimuistitettuja pätkiä.
    """
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    
    all_records = []
    current_start = start_dt
    
    total_days = (end_dt - start_dt).days or 1
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    while current_start < end_dt:
        current_end = min(current_start + timedelta(days=14), end_dt)
        from_str = current_start.strftime('%Y-%m-%d')
        to_str = current_end.strftime('%Y-%m-%d')
        
        elapsed_days = (current_start - start_dt).days
        progress_bar.progress(min(elapsed_days / total_days, 1.0))
        status_text.text(f"Fetching/Loading cached data: {from_str} to {to_str}...")
        
        # Haetaan pätkä (tulee välimuistista jos ladattu jo)
        chunk = fetch_entsog_chunk(from_str, to_str)
        all_records.extend(chunk)
        
        current_start = current_end + timedelta(days=1)
        
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(all_records)

def classify_flow(row):
    point_label = str(row.get('pointLabel', '')).lower()
    operator_label = str(row.get('operatorLabel', '')).lower()
    direction = str(row.get('directionKey', '')).lower()
    
    if direction == 'entry':
        if 'incukalns' in point_label or 'inčukalns' in point_label:
            return 'Inčukalns UGS (Withdrawal)'
        elif 'gipl' in point_label or 'santaka' in point_label:
            return 'GIPL (Poland -> LT)'
        elif 'klaipeda' in point_label or 'klaipėda' in point_label:
            if 'lng' in point_label or 'terminal' in point_label or 'amber' in operator_label or 'kn' in operator_label:
                return 'Klaipėda LNG'
        elif 'inkoo' in point_label or 'hamina' in point_label:
            return 'Inkoo & Hamina LNG'
            
    return None

# --- UI / Streamlit App ---

st.title("🔥 FinBalt Natural Gas Entry Flows")
st.markdown("Monthly gas supply volumes into the Finnish-Baltic regional gas market (TWh/month). Data source: **ENTSOG Transparency Platform**.")

st.sidebar.header("Settings")
months_to_fetch = st.sidebar.slider("Select time period (months):", min_value=3, max_value=24, value=6, step=1)

if st.sidebar.button("Clear Cache & Refresh 🔄"):
    st.cache_data.clear()
    st.rerun()

today = datetime.today()
first_day_current_month = today.replace(day=1)
start_dt = (first_day_current_month - timedelta(days=months_to_fetch * 31)).replace(day=1)

start_date = start_dt.strftime('%Y-%m-%d')
end_date = today.strftime('%Y-%m-%d')

# Haetaan data uutta pätkä-välimuistia hyödyntäen
df_raw = get_all_entsog_data(start_date, end_date)

if df_raw.empty:
    st.warning("No data retrieved from ENTSOG API. Please try again or reduce the selected range.")
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
        df_filtered['Date_Parsed'] = pd.to_datetime(df_filtered[date_col], utc=True)
        df_filtered['Month'] = df_filtered['Date_Parsed'].dt.strftime('%Y-%m')
        
        monthly_summary = df_filtered.groupby(['Month', 'Category'])['value'].sum().reset_index()
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
