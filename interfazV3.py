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
    right_now = datetime.now(pytz.timezone(TIMEZONE))
    today = right_now.date()
    weather_today, weather_emoji = get_current_weather()
    is_holiday_today, in_exams_today = get_calendar_context(today)

    # 1. PREPARAR PREDICCIÓN DE HOY
    today_range = pd.date_range(start=f"{today} 08:00", end=f"{today} 20:00", freq='10min', tz=TIMEZONE)
    df_pred = pd.DataFrame({'time_10m': today_range})
    df_pred['minutes_day'] = df_pred['time_10m'].dt.hour * 60 + df_pred['time_10m'].dt.minute
    df_pred['day_of_week'] = today.weekday()
    df_pred['is_holiday'] = is_holiday_today
    df_pred['in_exams'] = in_exams_today
    df_pred['rainy_weather'] = weather_today
    df_pred['is_break'] = df_pred['minutes_day'].apply(detect_break)

    # Procesar las clases de HOY recibidas del Google Sheet 'clases_hoy'
    if not df_today_classes.empty:
        df_today_classes['time_text'] = pd.to_datetime(df_today_classes['Hora'].astype(str)).dt.strftime('%H:%M')
        df_pred['time_text'] = df_pred['time_10m'].dt.strftime('%H:%M')
        df_pred = pd.merge(df_pred, df_today_classes[['time_text', 'Aulas_Ocupadas']], on='time_text', how='left')
        df_pred.rename(columns={'Aulas_Ocupadas': 'Occupied_Classrooms'}, inplace=True)
        df_pred['Occupied_Classrooms'] = df_pred['Occupied_Classrooms'].fillna(0)
        df_pred = df_pred.drop(columns=['time_text'])
    else:
        df_pred['Occupied_Classrooms'] = 0
    
    # Generar Predicciones
    df_pred['Prediction'] = ai_model.predict(df_pred[['minutes_day', 'day_of_week', 'Occupied_Classrooms', 'is_break', 'is_holiday', 'in_exams', 'rainy_weather']])
    df_pred['Prediction'] = pd.Series(np.maximum(0, df_pred['Prediction'])).rolling(window=2, min_periods=1).mean()
    if is_holiday_today: df_pred['Prediction'] *= 0.05

    tab1, tab2 = st.tabs(["Executive Dashboard (Today)", "Historical Data Inspector"])

    # --- PESTAÑA 1: EXECUTIVE DASHBOARD ---
    with tab1:
        last_record = df_history['time_10m'].max()
        inactive_minutes = (right_now - last_record).total_seconds() / 60

        st.markdown(f"**Context:** {'Holiday' if is_holiday_today else ('Exams' if in_exams_today else 'School Day')} | **Weather:** {'🌧️ Rain' if weather_today == 1 else '☀️ Clear'}")
        
        if inactive_minutes > 30:
            st.error(f"🔌 **Sensor offline:** Last data received on {last_record.strftime('%m/%d/%Y at %H:%M')}.")
        else:
            st.success(f"🟢 **Sensor online:** Updated at {last_record.strftime('%H:%M')}.")

        cutoff_time = right_now.replace(minute=(right_now.minute // 10) * 10, second=0, microsecond=0)
        df_actual_today = df_history[df_history['time_10m'].dt.date == today].copy()
        df_eval = pd.merge(df_pred[df_pred['time_10m'] <= cutoff_time], df_actual_today[['time_10m', PEOPLE_FIELD]], on='time_10m', how='inner')
        
        if not df_eval.empty:
            actual_partial = int(df_eval[PEOPLE_FIELD].sum())
            ai_partial = int(df_eval['Prediction'].sum())
            diff_pct = ((actual_partial - ai_partial) / ai_partial * 100) if ai_partial > 0 else 0
            if diff_pct > 15: status_light, sub_status = "🔴 HIGH TRAFFIC", f"+{int(diff_pct)}%"
            elif diff_pct < -15: status_light, sub_status = "🔵 QUIET DAY", f"{int(diff_pct)}%"
            else: status_light, sub_status = "🟢 NORMAL", "Matches AI"
            error_mae = abs(df_eval[PEOPLE_FIELD] - df_eval['Prediction']).mean()
            actual_peak = int(df_eval[PEOPLE_FIELD].max())
            peak_time = df_eval.loc[df_eval[PEOPLE_FIELD].idxmax(), 'time_10m'].strftime('%H:%M')
        else:
            status_light, sub_status, actual_partial, ai_partial, error_mae, actual_peak, peak_time = "⚪ WAITING", "Loading...", 0, 0, 0, 0, "--:--"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Status", status_light, sub_status)
        c2.metric("People (Sensor)", actual_partial, f"AI: {ai_partial}")
        c3.metric("Highest Actual Peak", f"{actual_peak} people", f"At {peak_time}")
        c4.metric("Avg. Deviation (AI)", f"± {int(error_mae)}", "people/10min")

        st.divider()
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(        
            x=df_pred['time_10m'], y=df_pred['Occupied_Classrooms'], 
            name='Active Classrooms', yaxis='y2', mode='lines', line_shape='hv', 
            fill='tozeroy', line=dict(color='rgba(255, 165, 0, 0.8)', width=2), fillcolor='rgba(255, 165, 0, 0.15)'
        ))
        
        fig.add_trace(go.Scatter(x=df_pred['time_10m'], y=df_pred['Prediction'], name='AI', line=dict(color='#9467bd', width=4)))
        if not df_actual_today.empty:
            fig.add_trace(go.Scatter(x=df_actual_today['time_10m'], y=df_actual_today[PEOPLE_FIELD], name='Actual', mode='lines+markers', line=dict(color='#1f77b4')))
        
        fig.update_layout(height=500, xaxis=dict(title="Time"), yaxis=dict(title="People", side='left'), yaxis2=dict(title="Classrooms", side='right', overlaying='y', range=[0, 8]), hovermode="x unified", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)

    # --- PESTAÑA 2: HISTORICAL INSPECTOR ---
    with tab2:
        st.markdown("### 🔍 Previous Days Explorer")
        hist_date = st.date_input("📅 Select date", value=today - timedelta(days=1), max_value=today)

        stats_cont = st.container()
        adv_stats_cont = st.container() 
        chart_cont = st.empty()
        selector_cont = st.container()

        with selector_cont:
            st.divider()
            view_mode = st.radio("⏱️ Select Time View", ["Working Hours (08:00 - 20:00)", "Full Day (24 Hours)"], horizontal=True)
            start_h, end_h = (8, 20) if "Working Hours" in view_mode else (0, 24)

        base_ts = pd.Timestamp(hist_date).tz_localize(TIMEZONE)
        start_time = base_ts + pd.Timedelta(hours=start_h)
        end_time = base_ts + pd.Timedelta(hours=end_h)
        backbone_end = end_time if end_h < 24 else end_time - pd.Timedelta(seconds=1)
        
        df_window = pd.DataFrame({'time_10m': pd.date_range(start=start_time, end=backbone_end, freq='10min')})

        day_mask = df_history['time_10m'].dt.date == hist_date
        df_actual_day = df_history[day_mask].copy()
        
        # 1. Unimos los datos del sensor
        df_window = pd.merge(df_window, df_actual_day, on='time_10m', how='left')

        # 🛠️ 2. SOLUCIÓN: Eliminamos las aulas incompletas y cruzamos con el Horario Maestro (df_schedule)
        df_window.drop(columns=['Occupied_Classrooms'], inplace=True, errors='ignore')
        df_window = pd.merge(df_window, df_schedule[['time_10m', 'Occupied_Classrooms']], on='time_10m', how='left')

        df_window['minutes_day'] = df_window['time_10m'].dt.hour * 60 + df_window['time_10m'].dt.minute
        df_window['day_of_week'] = hist_date.weekday()
        df_window['is_break'] = df_window['minutes_day'].apply(detect_break)
        is_hol_hist, in_ex_hist = get_calendar_context(hist_date)
        df_window['is_holiday'], df_window['in_exams'] = is_hol_hist, in_ex_hist
        df_window['Occupied_Classrooms'] = pd.to_numeric(df_window['Occupied_Classrooms'], errors='coerce').fillna(0)
        df_window['rainy_weather'] = df_window['rainy_weather'].fillna(0)

        df_window['Prediction'] = ai_model.predict(df_window[['minutes_day', 'day_of_week', 'Occupied_Classrooms', 'is_break', 'is_holiday', 'in_exams', 'rainy_weather']])
        df_window['Prediction'] = pd.Series(np.maximum(0, df_window['Prediction'])).rolling(window=2, min_periods=1).mean()
        if is_hol_hist == 1: df_window['Prediction'] *= 0.05

        df_real = df_window.dropna(subset=[PEOPLE_FIELD])
        with stats_cont:
            total_real = int(df_real[PEOPLE_FIELD].sum()) if not df_real.empty else 0
            
            # 1. AÑADIMOS ESTO: Sumamos la IA pero SOLO de las filas donde el sensor funcionó
            total_ai = int(df_real['Prediction'].sum()) if not df_real.empty else 0
            
            max_real = int(df_real[PEOPLE_FIELD].max()) if not df_real.empty else 0
            max_time = df_real.loc[df_real[PEOPLE_FIELD].idxmax(), 'time_10m'].strftime('%H:%M') if not df_real.empty else "--:--"

            m1, m2, m3, m4 = st.columns(4)
            
            # 2. MODIFICAMOS ESTO: Añadimos la predicción en el tercer parámetro (delta)
            m1.metric("Total People", f"{total_real}", f"AI predicted: {total_ai}", delta_color="off")
            
            m2.metric("Maximum Peak", f"{max_real} ppl", f"At {max_time}")
            m3.metric("Max. Classrooms", f"{int(df_window['Occupied_Classrooms'].max())}")
            m4.metric("Weather", "Rain 🌧️" if df_window['rainy_weather'].max() == 1 else "Clear ☀️")
        with adv_stats_cont:
            st.markdown("##### 🔬 Advanced Daily Analysis")
            if not df_real.empty:
                error_mae_hist = abs(df_real[PEOPLE_FIELD] - df_window.loc[df_real.index, 'Prediction']).mean()
                
                is_break_bool = df_window['is_break'].astype(bool)
                extended_break = is_break_bool | is_break_bool.shift(1).fillna(False) | is_break_bool.shift(-1).fillna(False)
                
                mask_real_breaks = extended_break.loc[df_real.index]
                break_flow = df_real[mask_real_breaks][PEOPLE_FIELD].sum()
                pct_breaks = (break_flow / total_real * 100) if total_real > 0 else 0

                ai_p = df_window.loc[df_real.index, 'Prediction'].sum()
                diff_pct_hist = ((total_real - ai_p) / ai_p * 100) if ai_p > 0 else 0

                e1, e2 = st.columns(2)
                with e1:
                    st.info(f"**AI Accuracy**\n\nDeviation: {diff_pct_hist:+.1f}%\n\nError: ±{int(error_mae_hist)} ppl")
                with e2:
                    st.success(f"**Movement**\n\nTransitions & Breaks: {int(pct_breaks)}%\n\nStrictly Class Time: {100 - int(pct_breaks)}%")
            else:
                st.warning("⚠️ No sensor data recorded for this time range.")
        
        fig_h = go.Figure()
        fig_h.add_trace(go.Bar(x=df_window['time_10m'], y=df_window['Occupied_Classrooms'], name='Classrooms', yaxis='y2', marker_color='rgba(255, 165, 0, 0.2)', marker_line_width=0))
        fig_h.add_trace(go.Scatter(x=df_window['time_10m'], y=df_window['Prediction'], name='AI Prediction', line=dict(color='#9467bd', width=3, dash='dot')))
        if not df_real.empty:
            fig_h.add_trace(go.Scatter(x=df_real['time_10m'], y=df_real[PEOPLE_FIELD], name='Actual Flow', mode='lines+markers', line=dict(color='#1f77b4', width=2)))
        
        fig_h.update_layout(height=400, xaxis=dict(title="Time", range=[start_time, end_time], tickformat="%H:%M"), yaxis=dict(title="People"), yaxis2=dict(title="Classrooms", side='right', overlaying='y', range=[0, 8]), hovermode="x unified", legend=dict(orientation="h", y=1.1))
        chart_cont.plotly_chart(fig_h, use_container_width=True)
        # --- GRÁFICA FINA DE LLUVIA (Debajo de la principal) ---
        st.markdown("##### 🌧️ Registro de Lluvia")
        fig_rain = go.Figure()
        
        # Usamos un gráfico de área para que quede como una franja continua azul
        fig_rain.add_trace(go.Scatter(
            x=df_window['time_10m'], 
            y=df_window['rainy_weather'], 
            fill='tozeroy',
            mode='lines',
            line=dict(color='rgba(0, 191, 255, 0.8)', width=2, shape='hv'), # 'hv' hace escalones perfectos
            fillcolor='rgba(0, 191, 255, 0.3)',
            name='Lluvia'
        ))
        
        fig_rain.update_layout(
            height=150, # ⬅️ Altura muy fina para que no estorbe
            margin=dict(l=0, r=0, t=10, b=0), # Quitamos márgenes
            yaxis=dict(
                tickmode='array',
                tickvals=[0, 1], 
                ticktext=['Seco ☀️', 'Lluvia 🌧️'], 
                range=[0, 1.2], # Un poco de margen por arriba
                fixedrange=True # Evita que el usuario haga zoom vertical y lo rompa
            ),
            xaxis=dict(range=[start_time, end_time], tickformat="%H:%M"),
            hovermode="x unified",
            showlegend=False
        )
        
        st.plotly_chart(fig_rain, use_container_width=True)
