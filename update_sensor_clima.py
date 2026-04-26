import requests
import json
import os
import gspread
import pytz
from datetime import datetime
from google.oauth2.service_account import Credentials

# ==========================================
# ⚙️ CONFIGURACIÓN
# ==========================================
ID_EXCEL_SENSOR = "1obld-nMYrcctyG-yciteVyQJUjLO6X2mpS3ue3dDcdw" # <--- SUSTITUYE POR EL ID DE TU GOOGLE SHEET
CHANNEL_ID = os.environ["THINGSPEAK_CHANNEL_ID"]
READ_API_KEY = os.environ["THINGSPEAK_READ_KEY"]
LAT, LON = "43.304654", "-2.009873"

def main():
    print("📡 Iniciando actualización de historial_sensor...")
    try:
        # 1. Obtener clima actual (Open-Meteo)
        res_w = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&current=rain,showers&timezone=auto"
        ).json()
        llueve = 1 if (res_w.get('current', {}).get('rain', 0) + res_w.get('current', {}).get('showers', 0)) > 0 else 0
        
        # 2. Obtener datos de ThingSpeak (últimos 20 resultados)
        url_ts = f"https://api.thingspeak.com/channels/{CHANNEL_ID}/feeds.json?api_key={READ_API_KEY}&results=20"
        feeds = requests.get(url_ts).json().get('feeds', [])
        
        datos_para_subir = []
        for f in feeds:
            # Formatear la fecha para que coincida con tu historial: 2026-04-10 09:38:35+00:00
            fecha_original = f['created_at'].replace('T', ' ').replace('Z', '+00:00')
            
            # Construcción de la fila de 10 columnas:
            # [0]created_at, [1]entry_id, [2]field1, [3]field2, [4]field3, 
            # [5]latitude, [6]longitude, [7]elevation, [8]status, [9]clima_lluvia
            fila = [
                fecha_original,    # Columna A
                f.get('entry_id'), # Columna B
                f.get('field1'),   # Columna C
                "",                # Columna D (field2 vacio)
                "",                # Columna E (field3 vacio)
                "",                # Columna F (latitude vacio)
                "",                # Columna G (longitude vacio)
                "",                # Columna H (elevation vacio)
                "",                # Columna I (status vacio)
                llueve             # Columna J (clima_lluvia)
            ]
            datos_para_subir.append(fila)

        if datos_para_subir:
            # 3. Conexión a Google Sheets
            creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
            creds = Credentials.from_service_account_info(
                creds_dict, 
                scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            )
            client = gspread.authorize(creds)
            
            # Abrimos el Excel y usamos .sheet1 (primera pestaña)
            sheet = client.open_by_key(ID_EXCEL_SENSOR).sheet1
            
            # Subimos todas las filas de golpe
            sheet.append_rows(datos_para_subir, value_input_option='USER_ENTERED')
            print(f"✅ Éxito: se han añadido {len(datos_para_subir)} filas a historial_sensor.")
        else:
            print("⚠️ No se encontraron nuevos feeds en ThingSpeak.")
            
    except Exception as e:
        print(f"❌ ERROR en el script del sensor: {e}")

if __name__ == "__main__":
    main()
