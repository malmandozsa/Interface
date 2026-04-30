import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pytz
from sklearn.ensemble import RandomForestRegressor
from streamlit_autorefresh import st_autorefresh
from streamlit_gsheets import GSheetsConnection

# ==========================================
# ⚙️ CONFIGURACIÓN GENERAL
# ==========================================
CHANNEL_ID = st.secrets["thingspeak_channel"]
READ_API_KEY = st.secrets["thingspeak_key"] 
PEOPLE_FIELD = "field1"
TIMEZONE = "Europe/Madrid" 
LATITUDE, LONGITUDE = "43.304654", "-2.009873"

TIME_SLOTS = [
    {"start": 900,  "end": 1020}, {"start": 1030, "end": 1150},
    {"start": 1205, "end": 1325}, {"start": 1500, "end": 1620},
    {"start": 1630, "end": 1750}, {"start": 1805, "end": 1925}
]

st.set_page_config(page_title="Tecnun Flow AI", layout="wide", page_icon="📊")

# Refrescar automáticamente cada 10 minutos
st_autorefresh(interval=600000, key="data_refresh")

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stMetricValue"] { font-size: 24px; }
    [data-testid="stMetric"] {
        border: 1px solid #666666;
        padding: 15px;
        border-radius: 10px;
        min-height: 120px;
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 📅 FUNCIONES DE APOYO
# ==========================================
@st.cache_data(ttl=1800)
def get_current_weather():
    try: 
        code = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&current_weather=true").json()['current_weather']['weathercode']
        if code <= 2: emoji = "☀️"
        elif code <= 48: emoji = "☁️"
        else: emoji = "🌧️"
        rain = 1 if code >= 50 else 0
        return rain, emoji
    except: 
        return 0, "❓"

def get_calendar_context(target_date):
    date_str = pd.to_datetime(target_date).strftime('%Y-%m-%d')
    holidays = ["2025-10-12", "2025-11-01", "2025-12-06", "2025-12-08", "2026-01-20", "2026-01-28", "2026-03-19", "2026-03-20", "2026-05-01", "2026-06-26"]
    if ("2025-12-22" <= date_str <= "2026-01-06") or ("2026-03-30" <= date_str <= "2026-04-08") or (date_str in holidays) or pd.to_datetime(date_str).weekday() >= 5: return 1, 0  
    if ("2025-12-09" <= date_str <= "2025-12-20") or ("2026-05-11" <= date_str <= "2026-05-23"): return 0, 1  
    return 0, 0 

def detect_break(day_minutes):
    for t in TIME_SLOTS:
        if abs(day_minutes - ((t['start']//100)*60 + t['start']%100)) <= 10 or abs(day_minutes - ((t['end']//100)*60 + t['end']%100)) <= 10: return 1
    return 0

@st.cache_data(ttl=600)
def load_data_and_train():
    # 1. LEER EL HISTÓRICO COMPLETO DEL SENSOR (Con lluvia real)
    try:
        # URL directa a tu archivo 'historial_sensor'
        url_sensor = "https://docs.google.com/spreadsheets/d/1obld-nMYrcctyG-yciteVyQJUjLO6X2mpS3ue3dDcdw/export?format=csv"
        df_hist = pd.read_csv(url_sensor)
        
        df_hist['created_at'] = pd.to_datetime(df_hist['created_at'])
        # Tu archivo guarda la gente en 'field1' y la lluvia en 'clima_lluvia'
        df_hist[PEOPLE_FIELD] = pd.to_numeric(df_hist['field1'], errors='coerce').fillna(0)
        df_hist['rainy_weather'] = pd.to_numeric(df_hist['clima_lluvia'], errors='coerce').fillna(0)
        
        df_hist = df_hist[['created_at', PEOPLE_FIELD, 'rainy_weather']]
    except Exception as e:
        st.error(f"❌ Error leyendo el historial del sensor: {repr(e)}")
        df_hist = pd.DataFrame()

    # 2. LEER DE THINGSPEAK (Solo para tener los datos de HOY en tiempo real)
    ts_url = f"https://api.thingspeak.com/channels/{CHANNEL_ID}/feeds.json?api_key={READ_API_KEY}&results=8000"
    try:
        ts_data = requests.get(ts_url).json()
        df_live = pd.DataFrame(ts_data['feeds'])
        df_live['created_at'] = pd.to_datetime(df_live['created_at'])
        df_live[PEOPLE_FIELD] = pd.to_numeric(df_live[PEOPLE_FIELD], errors='coerce').fillna(0)
        df_live['rainy_weather'] = 0 # Se pisará con datos reales si ya están en el histórico
        df_live = df_live[['created_at', PEOPLE_FIELD, 'rainy_weather']]
    except Exception as e:
        st.error(f"❌ Error en ThingSpeak: {repr(e)}")
        df_live = pd.DataFrame()

    # UNIR HISTORIAL DEL EXCEL CON EL DÍA DE HOY (ThingSpeak)
    df_h_raw = pd.concat([df_hist, df_live], ignore_index=True)
    # Eliminamos duplicados por si hay datos repetidos entre el Excel y ThingSpeak
    df_h_raw = df_h_raw.drop_duplicates(subset=['created_at'], keep='first')
    
    # Agrupar cada 10 minutos
    df_h_raw['time_10m'] = df_h_raw['created_at'].dt.tz_convert(TIMEZONE).dt.floor('10min')
    df_h = df_h_raw.groupby('time_10m').agg({
        PEOPLE_FIELD: 'sum',
        'rainy_weather': 'max' # Coge 1 si llovió en algún momento de esos 10 minutos
    }).reset_index()

    # 3. LEER DE GOOGLE SHEETS (Clases y Horarios)
    try:
        url_historial = "https://docs.google.com/spreadsheets/d/1RIsVJYe6PuPZsv7VU2gf4F3jla9P_SzH8UegBQvuf40/export?format=csv"
        df_c_full = pd.read_csv(url_historial)
        
        url_hoy = "https://docs.google.com/spreadsheets/d/1oe6rvKg1zo-Jv7Nd8FJy0FEXolN4yvg7KnaNAAsIs94/export?format=csv"
        df_hoy = pd.read_csv(url_hoy)

        df_c_raw = pd.concat([
            df_c_full[['Fecha', 'Hora', 'Aulas_Ocupadas']],
            df_hoy[['Fecha', 'Hora', 'Aulas_Ocupadas']]
        ], ignore_index=True)

        df_c_raw.columns = ['Date', 'Time', 'Occupied_Classrooms']
        df_c_raw['time_10m'] = pd.to_datetime(pd.to_datetime(df_c_raw['Date']).dt.strftime('%Y-%m-%d') + ' ' + df_c_raw['Time'].astype(str)).dt.tz_localize(TIMEZONE, ambiguous='NaT', nonexistent='NaT').dt.floor('10min')
        df_schedule = df_c_raw.groupby('time_10m')['Occupied_Classrooms'].max().reset_index()

    except Exception as e:
        st.error(f"❌ Error en Excels de clases: {repr(e)}")
        return None, None, None, None

    # --- UNIÓN FINAL Y ENTRENAMIENTO ---
    df = pd.merge(df_h, df_schedule, on='time_10m', how='left')
    df['Occupied_Classrooms'] = df['Occupied_Classrooms'].fillna(0)
    
    if df.empty: return None, None, None, None
    
    df['minutes_day'] = df['time_10m'].dt.hour * 60 + df['time_10m'].dt.minute
    df['day_of_week'] = df['time_10m'].dt.weekday
    df['is_break'] = df['minutes_day'].apply(detect_break)
    df[['is_holiday', 'in_exams']] = pd.DataFrame(df['time_10m'].dt.date.apply(get_calendar_context).tolist(), index=df.index)
    
    # ENTRENAR MODELO
    X = df[['minutes_day', 'day_of_week', 'Occupied_Classrooms', 'is_break', 'is_holiday', 'in_exams', 'rainy_weather']]
    y = df[PEOPLE_FIELD]
    model = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42).fit(X, y)
    
    return df, model, df_hoy, df_schedule

# ==========================================
# 🖥️ DASHBOARD INTERFACE
# ==========================================
st.title("Tecnun Flow - AI Predictive Dashboard")

# Recibimos el horario maestro (df_schedule) en la cuarta variable
df_history, ai_model, df_today_classes, df_schedule = load_data_and_train()

if df_history is None:
    st.error("Critical error: Could not load data from APIs.")
else:
    # Añadimos la predicción al historial para que la Pestaña 2 funcione correctamente
    df_history['Prediction'] = ai_model.predict(df_history[['minutes_day', 'day_of_week', 'Occupied_Classrooms', 'is_break', 'is_holiday', 'in_exams', 'rainy_weather']])
    
    right_now = datetime.now(pytz.timezone(TIMEZONE))
    today = right_now.date()
    weather_today, weather_emoji = get_current_weather()
    is_holiday_today, in_exams_today = get_calendar_context(today)

    # 1. PREPARAR PREDICCIÓN DE HOY
    today_range = pd.date_range(start=f"{today} 08:00", end=f"{today} 20:00
