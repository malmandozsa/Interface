import pandas as pd
import requests
from datetime import datetime
import pytz
import os
import json
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# ⚙️ CONFIGURACIÓN
# ==========================================
ZONA_HORARIA = "Europe/Madrid" 
IDS_AULAS = [1282, 1283, 1284, 1285, 1286, 1287, 1531, 1535]
URL_WU = "https://unav.webuntis.com/WebUntis/api/public/timetable/weekly/data"

FRANJAS_REALES = [
    {"inicio": 900,  "fin": 1020}, {"inicio": 1030, "fin": 1150},
    {"inicio": 1205, "fin": 1325}, {"inicio": 1500, "fin": 1620},
    {"inicio": 1630, "fin": 1750}, {"inicio": 1805, "fin": 1925}
]

def a_minutos(hora_wu):
    return (hora_wu // 100) * 60 + (hora_wu % 100)

def extraer_datos_hoy():
    print("🚀 Iniciando extracción de WebUntis para el día de hoy...")
    hoy = datetime.now(pytz.timezone(ZONA_HORARIA)).date()
    fecha_str = hoy.strftime("%Y-%m-%d")
    fecha_int = int(hoy.strftime("%Y%m%d"))
    headers = {'User-Agent': 'Mozilla/5.0'}
    eventos = []
    
    for aula_id in IDS_AULAS:
        try:
            res = requests.get(URL_WU, params={"elementType": "4", "elementId": aula_id, "date": fecha_str}, headers=headers)
            if res.status_code == 200:
                clases = res.json().get("data", {}).get("result", {}).get("data", {}).get("elementPeriods", {}).get(str(aula_id), [])
                for c in clases:
                    if c.get('date') == fecha_int:
                        eventos.append({'aula_id': aula_id, 'inicio': c.get('startTime'), 'fin': c.get('endTime')})
        except: 
            pass
            
    datos_hoy = []
    rango_tiempo = pd.date_range(start=f"{fecha_str} 08:00", end=f"{fecha_str} 20:00", freq='10min')
    
    if eventos:
        df_ev = pd.DataFrame(eventos).groupby(['aula_id']).agg({'inicio': 'min', 'fin': 'max'}).reset_index()
        for tiempo in rango_tiempo:
            minutos_actuales = tiempo.hour * 60 + tiempo.minute
            aulas_ocupadas = 0
            for f in FRANJAS_REALES:
                if a_minutos(f["inicio"]) <= minutos_actuales < a_minutos(f["fin"]):
                    aulas_ocupadas = sum(1 for _, c in df_ev.iterrows() if a_minutos(c["inicio"]) < a_minutos(f["fin"]) and a_minutos(c["fin"]) > a_minutos(f["inicio"]))
                    break
            datos_hoy.append({'time_10m': tiempo, 'Aulas_Ocupadas': aulas_ocupadas})
    else:
        for tiempo in rango_tiempo:
            datos_hoy.append({'time_10m': tiempo, 'Aulas_Ocupadas': 0})
            
    # 1. Convertimos los datos en DataFrame
    df_final = pd.DataFrame(datos_hoy)
    
    # 2. Formateamos a las columnas que necesita nuestro Google Sheet
    df_final['Fecha'] = df_final['time_10m'].dt.strftime('%Y-%m-%d')
    df_final['Hora'] = df_final['time_10m'].dt.strftime('%H:%M')
    
    # 3. Ordenamos las columnas: Fecha, Hora, Aulas_Ocupadas
    df_upload = df_final[['Fecha', 'Hora', 'Aulas_Ocupadas']]
    
    # 4. Lo convertimos a una lista de listas (el formato que entiende Google Sheets)
    valores_para_subir = df_upload.values.tolist()
    
    return valores_para_subir

def subir_a_google_sheets(datos):
    print("☁️ Conectando a Google Sheets...")
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        
        # El robot usará el ID directo, que es infalible
        spreadsheet = client.open_by_key("1oe6rvKg1zo-Jv7Nd8FJy0FEXolN4yvg7KnaNAAsIs94")
        worksheet = spreadsheet.worksheet("Historial")
        
        # ⚠️ TRUCO: Limpiamos los datos para que sean texto y números puros de Python
        # Así evitamos que Google Sheets se atragante con formatos extraños
        datos_limpios = [[str(fila[0]), str(fila[1]), int(fila[2])] for fila in datos]
        
        # Le decimos a Google que inserte los datos como si los tecleara un humano (USER_ENTERED)
        worksheet.append_rows(datos_limpios, value_input_option='USER_ENTERED')
        print(f"✅ ¡Éxito! Se han añadido {len(datos_limpios)} filas a la base de datos.")
        
    except KeyError:
        print("❌ ERROR: No se encontró la variable de entorno GOOGLE_CREDENTIALS. ¿Estás en GitHub Actions?")
    except Exception as e:
        # Usamos repr(e) para que nos dé el error detallado si vuelve a fallar
        print(f"❌ ERROR al subir a Google Sheets: {repr(e)}")

if __name__ == "__main__":
    datos_nuevos = extraer_datos_hoy()
    if datos_nuevos:
        subir_a_google_sheets(datos_nuevos)
    else:
        print("⚠️ No se generaron datos para subir.")
