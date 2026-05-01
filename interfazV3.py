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
        st.subheader("📊 Historical Data Inspector")

        # 1. Selector de fecha
        hist_date = st.date_input("Select a date to inspect:", df_history['time_10m'].max().date())

        # 2. Filtrado de datos del día completo
        df_window = df_history[df_history['time_10m'].dt.date == hist_date].copy()

        if df_window.empty:
            st.warning(f"No data found for {hist_date} in the history.")
        else:
            # --- 3. RESERVAMOS LOS HUECOS VISUALES EN EL ORDEN QUE QUIERES ---
            hueco_metricas = st.container()         # Arriba del todo: las métricas numéricas
            hueco_grafica_principal = st.empty()    # En medio arriba: la gráfica de datos
            hueco_lluvia = st.empty()               # En medio abajo: la gráfica de lluvia
            
            # --- 4. DIBUJAMOS LOS BOTONES (Se verán abajo del todo) ---
            st.markdown("---") # Línea separadora opcional
            if 'time_filter' not in st.session_state:
                st.session_state['time_filter'] = 'lectivo'

            t1, t2 = st.columns(2)
            with t1:
                if st.button("8:00 - 20:00", use_container_width=True):
                    st.session_state['time_filter'] = 'lectivo'
            with t2:
                if st.button("00:00 - 24:00", use_container_width=True):
                    st.session_state['time_filter'] = '24h'

            if st.session_state['time_filter'] == 'lectivo':
                start_h, end_h = 8, 20
            else:
                start_h, end_h = 0, 23

            # --- 5. CALCULAMOS LOS DATOS FILTRADOS (df_real) ---
            start_time = pd.Timestamp.combine(hist_date, pd.Timestamp(f"{start_h}:00").time()).tz_localize(TIMEZONE)
            end_time = pd.Timestamp.combine(hist_date, pd.Timestamp(f"{end_h}:59").time()).tz_localize(TIMEZONE)
            
            df_real = df_window[(df_window['time_10m'] >= start_time) & (df_window['time_10m'] <= end_time)]

            # --- 6. AHORA MANDAMOS CADA COSA A SU HUECO CORRESPONDIENTE ---

            # A. Llenamos el hueco de las métricas
            with hueco_metricas:
                total_real = int(df_real[PEOPLE_FIELD].sum()) if not df_real.empty else 0
                total_ai = int(df_real['Prediction'].sum()) if not df_real.empty else 0
                
                max_real = int(df_real[PEOPLE_FIELD].max()) if not df_real.empty else 0
                max_time = df_real.loc[df_real[PEOPLE_FIELD].idxmax(), 'time_10m'].strftime('%H:%M') if not df_real.empty else "--:--"

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total People", f"{total_real}", f"AI Predicted: {total_ai}", delta_color="off")
                m2.metric("Maximum Peak", f"{max_real} ppl", f"At {max_time}")
                m3.metric("Max. Classrooms", f"{int(df_window['Occupied_Classrooms'].max())}")
                m4.metric("Weather", "Rain 🌧️" if df_real['rainy_weather'].max() > 0 else "Clear ☀️")

            # B. Llenamos el hueco de la Gráfica Principal (Gente vs Aulas)
            fig_h = go.Figure()
            fig_h.add_trace(go.Bar(
                x=df_real['time_10m'], y=df_real['Occupied_Classrooms'],
                name="Occupied Classrooms", marker_color='rgba(255, 165, 0, 0.3)', yaxis='y2'
            ))
            fig_h.add_trace(go.Scatter(
                x=df_real['time_10m'], y=df_real[PEOPLE_FIELD],
                name="Real People Count", line=dict(color='firebrick', width=3)
            ))
            fig_h.add_trace(go.Scatter(
                x=df_real['time_10m'], y=df_real['Prediction'],
                name="AI Prediction", line=dict(color='royalblue', width=2, dash='dot')
            ))
            fig_h.update_layout(
                title=f"Activity details for {hist_date}",
                xaxis_title="Time",
                yaxis_title="People Count",
                yaxis2=dict(title="Classrooms", overlaying='y', side='right', range=[0, 10]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                hovermode="x unified",
                height=400
            )
            with hueco_grafica_principal:
                st.plotly_chart(fig_h, use_container_width=True)

            # C. Llenamos el hueco de la Gráfica de Lluvia
            fig_rain = go.Figure()
            fig_rain.add_trace(go.Scatter(
                x=df_real['time_10m'],       
                y=df_real['rainy_weather'],  
                fill='tozeroy',
                mode='lines+markers',
                line=dict(color='rgba(0, 191, 255, 0.8)', width=2, shape='hv'),
                fillcolor='rgba(0, 191, 255, 0.3)',
                name='Rain'
            ))
            fig_rain.update_layout(
                height=130, 
                margin=dict(l=0, r=0, t=10, b=0),
                yaxis=dict(
                    tickmode='array',
                    tickvals=[0, 1], 
                    ticktext=['Dry ☀️', 'Rain 🌧️'], 
                    range=[0, 1.2],
                    fixedrange=True
                ),
                xaxis=dict(showticklabels=False), # Quitamos etiquetas X para que quede más limpio debajo de la principal
                hovermode="x unified",
                showlegend=False
            )
            
            with hueco_lluvia.container():
                st.markdown("##### 🌧️ Rain Tracker")
                st.plotly_chart(fig_rain, use_container_width=True)
