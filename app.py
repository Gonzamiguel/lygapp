import json
import os
from typing import Dict, List, Optional, TypedDict

import google.generativeai as genai
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# --- 1. CONFIGURACIÓN DE L&G ---
SPREADSHEET_ID = st.secrets.get("SPREADSHEET_ID", "16ebPbHF_KIsSDgxX39diN04_69-osZDJey_OMAefLU8")

SHEET_CONFIG = {
    "factura": ("Facturas", ["Fecha", "Proveedor", "CUIT", "Nro Factura", "Producto/Servicio", "Cantidad", "Precio Unitario Neto", "IVA %", "Subtotal c/IVA", "Total Factura"]),
    "orden_compra": ("Orden de compra", ["Fecha", "Proveedor", "Nro OC", "Detalle", "Monto Total", "Condición de Pago"]),
    "orden_pago": ("Orden de pago", ["Fecha", "Proveedor", "Nro OP", "Facturas Canceladas", "Importe Bruto", "Retenciones", "Neto Pagado", "Medio de Pago"]),
}

def init_models():
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        st.error("Falta GOOGLE_API_KEY")
        st.stop()
    genai.configure(api_key=api_key)
    # Usamos Gemini 2.0 Flash por su estabilidad y cuota
    return genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"response_mime_type": "application/json", "temperature": 0.1}
    )

@st.cache_resource
def get_gspread_client():
    if os.path.exists("service_account.json"):
        creds = Credentials.from_service_account_file("service_account.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
    elif "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    else:
        return None
    return gspread.authorize(creds)

def call_ai(model, file_content, file_name):
    # --- INGENIERÍA DE PROMPT MEJORADA ---
    prompt = f"""
    Actúa como un Auditor Contable experto para L&G Logística. Tu objetivo es extraer datos del archivo '{file_name}' con un desglose DETALLADO FILA POR FILA.

    ### REGLAS GENERALES:
    - No resumas: Si hay múltiples ítems o facturas, genera un objeto en el array 'rows' por cada uno.
    - Repite los datos de cabecera (Fecha, Proveedor, CUIT, Nro de documento) en cada fila generada.
    - Los números deben usar punto decimal (ej: 140000.50).

    ### INSTRUCCIONES POR TIPO DE DOCUMENTO:

    1. FACTURA (factura):
       - Genera una fila por cada Producto o Servicio detallado.
       - Extrae: Fecha, Proveedor, CUIT, Nro Factura, Producto/Servicio, Cantidad, Precio Unitario Neto, IVA %, Subtotal c/IVA (Precio * Cant + IVA) y Total Factura.

    2. ORDEN DE COMPRA (orden_compra):
       - Genera una fila por cada ítem o descripción en la tabla de compra.
       - Extrae: Fecha, Proveedor, Nro OC, Detalle (de la línea), Monto Total (de la línea) y Condición de Pago (ej: E CHEQ 30D).

    3. ORDEN DE PAGO (orden_pago):
       - Genera una fila por cada factura cancelada que aparezca en la tabla de pagos.
       - Extrae: Fecha, Proveedor, Nro OP, Facturas Canceladas (el número de la factura individual), Importe Bruto (total de esa factura), Retenciones (extrae el monto de retención si aplica), Neto Pagado (monto final después de retenciones) y Medio de Pago (Banco/Cheque).

    ### FORMATO DE SALIDA (ESTRICTO JSON):
    {{
      "document_type": "factura" | "orden_compra" | "orden_pago",
      "rows": [
        {{ "Columna Exacta": "Valor" }}
      ]
    }}
    """
    
    response = model.generate_content([
        prompt,
        {"mime_type": "application/pdf", "data": file_content}
    ])
    return json.loads(response.text)

def main():
    st.set_page_config(page_title="L&G Logística - IA Desglose", layout="wide")
    st.title("🚀 Sistema de Carga L&G")
    st.markdown("---")
    
    if "extractions" not in st.session_state:
        st.session_state["extractions"] = []

    uploaded_files = st.file_uploader("Subir comprobantes PDF", type="pdf", accept_multiple_files=True)

    if st.button("🤖 Procesar con IA", type="primary", disabled=not uploaded_files):
        model = init_models()
        results = []
        
        for file in uploaded_files:
            with st.spinner(f"Analizando y desglosando {file.name}..."):
                try:
                    content = file.read()
                    raw_data = call_ai(model, content, file.name)
                    doc_type = raw_data.get("document_type")
                    
                    if doc_type not in SHEET_CONFIG:
                        st.warning(f"No se reconoció el tipo de documento en {file.name}")
                        continue
                        
                    sheet_name, headers = SHEET_CONFIG[doc_type]
                    
                    # Normalización de filas según headers de SHEET_CONFIG
                    clean_rows = []
                    for r in raw_data.get("rows", []):
                        row_data = [str(r.get(h, "")) for h in headers]
                        clean_rows.append(row_data)
                    
                    results.append({
                        "sheet": sheet_name, 
                        "headers": headers, 
                        "rows": clean_rows, 
                        "file": file.name
                    })
                except Exception as e:
                    st.error(f"Error en {file.name}: {e}")
        
        st.session_state["extractions"] = results

    # Visualización y Edición
    if st.session_state["extractions"]:
        st.subheader("📋 Revisión de Datos (Desglose por fila)")
        for idx, item in enumerate(st.session_state["extractions"]):
            with st.expander(f"📄 {item['file']} → {item['sheet']} ({len(item['rows'])} filas)", expanded=True):
                df = pd.DataFrame(item["rows"], columns=item["headers"])
                edited_df = st.data_editor(df, key=f"editor_{idx}", num_rows="dynamic")
                st.session_state["extractions"][idx]["rows"] = edited_df.values.tolist()

        if st.button("📤 Confirmar y Subir a Google Sheets"):
            client = get_gspread_client()
            if client:
                progress_bar = st.progress(0)
                try:
                    sh = client.open_by_key(SPREADSHEET_ID)
                    for i, item in enumerate(st.session_state["extractions"]):
                        ws = sh.worksheet(item["sheet"])
                        # Si la hoja está vacía, ponemos encabezados
                        if not ws.get_all_values():
                            ws.append_row(item["headers"])
                        
                        ws.append_rows(item["rows"], value_input_option="USER_ENTERED")
                        progress_bar.progress((i + 1) / len(st.session_state["extractions"]))
                    
                    st.success("¡Datos cargados con éxito!")
                    st.balloons()
                    st.session_state["extractions"] = [] 
                except Exception as e:
                    st.error(f"Error al subir: {e}")

if __name__ == "__main__":
    main()