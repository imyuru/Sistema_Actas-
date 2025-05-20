from flask import Flask, request, redirect, render_template, send_from_directory, Response
import os
import json
import csv
import re
from datetime import datetime
import mysql.connector
import pdfplumber
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Configuración de la base de datos
db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

@app.route('/')
def index(mensaje=None):
    # Conexión
    conn = mysql.connector.connect(**db_config)

    # Cursor principal para proyectos
    cursor = conn.cursor(dictionary=True, buffered=True)
    cursor.execute("SELECT * FROM Proyectos")
    proyectos = cursor.fetchall()
    cursor.close()

    # Obtener los datos relacionados por proyecto
    for proyecto in proyectos:
        pid = proyecto['IdProyecto']

        # Equipos
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT TipoEquipo, Marca, Modelo, Cantidad FROM Equipos WHERE IdProyecto = %s", (pid,))
        proyecto['Equipos'] = cur.fetchall()
        cur.close()
        
        # Pagos 
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT Etapa, Porcentaje, Monto, Pagado FROM AnexosDePagos WHERE IdProyecto = %s", (pid,))
        proyecto['Pagos'] = cur.fetchall()
        cur.close()

        # Contactos
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT TipoContacto, Nombre, Telefono, Correo FROM Contactos WHERE IdProyecto = %s", (pid,))
        proyecto['Contactos'] = cur.fetchall()
        cur.close()

        # Anexos
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT NombreAnexo FROM Anexos WHERE IdProyecto = %s", (pid,))
        proyecto['Anexos'] = cur.fetchall()
        cur.close()

        # Datos Bancarios
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT * FROM DatosBancarios WHERE IdProyecto = %s", (pid,))
        proyecto['Banco'] = cur.fetchone()
        cur.close()

        # Presupuesto (🆕 nueva sección)
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT Categoria, Monto FROM Presupuesto WHERE IdProyecto = %s", (pid,))
        proyecto['Presupuesto'] = cur.fetchall()
        cur.close()

    conn.close()

    return render_template('index.html', proyectos=proyectos, mensaje=mensaje)



@app.route('/pagos')
def ver_pagos():
    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor(dictionary=True, buffered=True)

    filtro_tipo = request.args.get('filtro_tipo', 'nombre')
    valor = request.args.get('valor', '').strip()

    if valor:
        if filtro_tipo == 'id' and valor.isdigit():
            query = """
                SELECT a.IdPago, a.IdProyecto, p.NombreProyecto,
                       a.Etapa, a.Porcentaje, a.Monto, a.Pagado,
                       a.ComprobanteArchivo
                FROM AnexosDePagos a
                JOIN Proyectos p ON p.IdProyecto = a.IdProyecto
                WHERE p.IdProyecto = %s
                ORDER BY p.NombreProyecto, a.IdPago
            """
            cur.execute(query, (int(valor),))
        else:
            query = """
                SELECT a.IdPago, a.IdProyecto, p.NombreProyecto,
                       a.Etapa, a.Porcentaje, a.Monto, a.Pagado,
                       a.ComprobanteArchivo
                FROM AnexosDePagos a
                JOIN Proyectos p ON p.IdProyecto = a.IdProyecto
                WHERE p.NombreProyecto LIKE %s
                ORDER BY p.NombreProyecto, a.IdPago
            """
            cur.execute(query, (f"%{valor}%",))
    else:
        query = """
            SELECT a.IdPago, a.IdProyecto, p.NombreProyecto,
                   a.Etapa, a.Porcentaje, a.Monto, a.Pagado,
                   a.ComprobanteArchivo
            FROM AnexosDePagos a
            JOIN Proyectos p ON p.IdProyecto = a.IdProyecto
            ORDER BY p.NombreProyecto, a.IdPago
        """
        cur.execute(query)

    pagos = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('pagos.html', pagos=pagos)

@app.route('/eliminar_proyecto', methods=['POST'])
def eliminar_proyecto():
    id = request.form.get('id_proyecto')
    if not id:
        return redirect('/')

    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor()

    # Eliminar registros relacionados primero por integridad referencial
    cur.execute("DELETE FROM Contactos WHERE IdProyecto = %s", (id,))
    cur.execute("DELETE FROM Equipos WHERE IdProyecto = %s", (id,))
    cur.execute("DELETE FROM AnexosDePagos WHERE IdProyecto = %s", (id,))
    cur.execute("DELETE FROM DatosBancarios WHERE IdProyecto = %s", (id,))
    cur.execute("DELETE FROM Anexos WHERE IdProyecto = %s", (id,))
    cur.execute("DELETE FROM Presupuesto WHERE IdProyecto = %s", (id,))

    # Finalmente eliminar el proyecto
    cur.execute("DELETE FROM Proyectos WHERE IdProyecto = %s", (id,))

    conn.commit()
    cur.close()
    conn.close()

    return redirect('/')


@app.route('/eliminar_comprobante/<int:id_pago>')
def eliminar_comprobante(id_pago):
    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor(dictionary=True)

    # Obtener archivo actual
    cur.execute("SELECT ComprobanteArchivo FROM AnexosDePagos WHERE IdPago = %s", (id_pago,))
    data = cur.fetchone()
    filename = data['ComprobanteArchivo']

    # Eliminar físicamente si existe
    if filename:
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(path):
            os.remove(path)

    # Limpiar base de datos
    cur.execute("UPDATE AnexosDePagos SET ComprobanteArchivo = NULL WHERE IdPago = %s", (id_pago,))
    conn.commit()
    cur.close()
    conn.close()

    return redirect('/pagos')

@app.route('/marcar_pagado/<int:id_pago>')
def marcar_pagado(id_pago):
    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor()

    cur.execute("UPDATE AnexosDePagos SET Pagado = TRUE WHERE IdPago = %s", (id_pago,))
    conn.commit()

    cur.close()
    conn.close()

    return "<script>window.location.href='/pagos';</script>"

@app.route('/marcar_pendiente/<int:id_pago>')
def marcar_pendiente(id_pago):
    conn = mysql.connector.connect(**db_config)
    cur = conn.cursor()

    cur.execute("UPDATE AnexosDePagos SET Pagado = FALSE WHERE IdPago = %s", (id_pago,))
    conn.commit()

    cur.close()
    conn.close()

    return "<script>window.location.href='/pagos';</script>"

@app.route('/subir_comprobante/<int:id_pago>', methods=['POST'])
def subir_comprobante(id_pago):
    if 'archivo' not in request.files:
        return "No se envió archivo", 400

    file = request.files['archivo']
    if file and allowed_file(file.filename):
        filename = f"comprobante_{id_pago}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        conn = mysql.connector.connect(**db_config)
        cur = conn.cursor()
        cur.execute("UPDATE AnexosDePagos SET ComprobanteArchivo = %s WHERE IdPago = %s", (filename, id_pago))
        conn.commit()
        cur.close()
        conn.close()

    return redirect('/pagos')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)



# OpenAI
# Inicializa cliente OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or (os.getenv('APIOpenAi')))

def parse_fecha(fecha_str):
    try:
        return datetime.strptime(fecha_str, "%d-%m-%Y").strftime("%Y-%m-%d")
    except:
        try:
            return datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        except:
            return None

def limpiar_decimal(valor):
    if isinstance(valor, (int, float)):
        return valor
    if not valor:
        return None
    limpio = re.sub(r"[^\d.,\-]", "", str(valor)).replace(",", ".")
    try:
        return float(limpio)
    except:
        return None
    


@app.route('/procesar_acta', methods=['GET', 'POST'])
def procesar_acta():
    mensaje = None
    if request.method == 'POST':
        file = request.files.get('archivo')
        if file and allowed_file(file.filename) and file.filename.endswith('.pdf'):
            try:
                # 1. Extraer texto del PDF
                with pdfplumber.open(file) as pdf:
                    texto = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])

                # 2. Prompt GPT-4.1 estructurado
                prompt = f"""
Analiza el siguiente texto de un acta de instalación de sistema solar fotovoltaico.

Devuelve toda la información estructurada en formato JSON válido. No incluyas texto adicional, solo el JSON.

El JSON debe tener esta estructura principal:

- Proyecto: objeto con:
  - NombreProyecto, FechaActa, FechaFirmaContrato, FechaLimite, Ubicacion,
  - DescripcionGeneral, VoltajeConexion, PotenciaInstalada, PresupuestoTotal,
  - Vendedor, ComisionTerceros, GerenteProyecto, PreparadoPor, Notas

- Contactos: lista de objetos con:
  - TipoContacto, Nombre, Telefono, Correo

- Equipos: lista de objetos con:
  - TipoEquipo, Marca, Modelo, Cantidad, PotenciaWp, Capacidad

- Pagos: lista de objetos con:
  - Etapa, Porcentaje, Monto, Pagado (true/false)

- DatosBancarios: objeto con:
  - NombreBanco, Cuentahabiente, Observaciones

- Anexos: lista de objetos con:
  - NombreAnexo
  
- Presupuesto: lista de objetos con:
  - Categoria (texto)
  - Monto (número o null si es "N/A")

Notas:
- Todas las fechas deben estar en formato YYYY-MM-DD (ejemplo: "2025-04-15").
- No incluyas unidades como "kW", "kWp", "B/." ni símbolos de porcentaje (%). Solo valores numéricos puros.
- Usa null si algún valor no está disponible.
- Agrupa múltiples elementos como vectores si hay más de uno (ej: Contactos, Pagos, Equipos).
- No incluyas texto fuera del JSON.

Texto del acta:
{texto}
"""

                # 3. Llamar a OpenAI
                response = client.chat.completions.create(
                    model="gpt-4.1",
                    messages=[
                        {"role": "system", "content": "Eres un asistente que estructura actas en JSON para bases de datos. Solo responde con JSON válido."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0
                )

                data = response.choices[0].message.content.strip()
                print("🔎 Respuesta de GPT:\n", data)

                if not data.startswith("{"):
                    raise Exception("La respuesta de GPT no comienza con '{'. JSON mal formado.")

                estructura = json.loads(data)

                if "Proyecto" not in estructura or "NombreProyecto" not in estructura["Proyecto"]:
                    raise Exception("La clave 'Proyecto.NombreProyecto' no está presente en el JSON.")

                # 4. Conexión a BD con buffer
                conn = mysql.connector.connect(buffered=True, **db_config)
                cur = conn.cursor()

                p = estructura["Proyecto"]
                cur.execute("""
                    INSERT INTO Proyectos (
                        NombreProyecto, FechaActa, FechaFirmaContrato, FechaLimite, Ubicacion,
                        DescripcionGeneral, VoltajeConexion, PotenciaInstalada, PresupuestoTotal,
                        Vendedor, ComisionTerceros, GerenteProyecto, PreparadoPor, Notas
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    p["NombreProyecto"],
                    parse_fecha(p["FechaActa"]),
                    parse_fecha(p["FechaFirmaContrato"]),
                    parse_fecha(p["FechaLimite"]),
                    p["Ubicacion"],
                    p["DescripcionGeneral"],
                    p["VoltajeConexion"],
                    limpiar_decimal(p["PotenciaInstalada"]),
                    limpiar_decimal(p["PresupuestoTotal"]),
                    p["Vendedor"],
                    p["ComisionTerceros"],
                    p["GerenteProyecto"],
                    p["PreparadoPor"],
                    p["Notas"]
                ))
                conn.commit()

                # Obtener ID del proyecto
                id_proyecto = cur.lastrowid

                # Contactos
                for c in estructura.get("Contactos", []):
                    cur.execute("INSERT INTO Contactos (IdProyecto, TipoContacto, Nombre, Telefono, Correo) VALUES (%s, %s, %s, %s, %s)",
                                (id_proyecto, c["TipoContacto"], c["Nombre"], c["Telefono"], c["Correo"]))

                # Equipos
                for eq in estructura.get("Equipos", []):
                    cur.execute("INSERT INTO Equipos (IdProyecto, TipoEquipo, Marca, Modelo, Cantidad, PotenciaWp, Capacidad) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                (id_proyecto, eq["TipoEquipo"], eq["Marca"], eq["Modelo"], eq["Cantidad"],
                                 limpiar_decimal(eq.get("PotenciaWp")), eq.get("Capacidad")))

                # Pagos
                for pago in estructura.get("Pagos", []):
                    cur.execute("INSERT INTO AnexosDePagos (IdProyecto, Etapa, Porcentaje, Monto, Pagado) VALUES (%s, %s, %s, %s, %s)",
                                (id_proyecto,
                                 pago["Etapa"],
                                 limpiar_decimal(pago["Porcentaje"]),
                                 limpiar_decimal(pago["Monto"]),
                                 pago["Pagado"]))

                # Datos bancarios
                db = estructura.get("DatosBancarios")
                if db:
                    cur.execute("INSERT INTO DatosBancarios (IdProyecto, NombreBanco, Cuentahabiente, Observaciones) VALUES (%s, %s, %s, %s)",
                                (id_proyecto, db["NombreBanco"], db["Cuentahabiente"], db["Observaciones"]))

                # Anexos
                for anexo in estructura.get("Anexos", []):
                    cur.execute("INSERT INTO Anexos (IdProyecto, NombreAnexo) VALUES (%s, %s)",
                                (id_proyecto, anexo["NombreAnexo"]))
                    
                # Presupuesto
                for item in estructura.get("Presupuesto", []):
                    cur.execute("INSERT INTO Presupuesto (IdProyecto, Categoria, Monto) VALUES (%s, %s, %s)",
                                (id_proyecto, item["Categoria"], limpiar_decimal(item["Monto"])))


                conn.commit()
                cur.close()
                conn.close()

                mensaje = "✅ Acta procesada y guardada con éxito."

            except Exception as e:
                print("❌ Error en el procesamiento:", e)
                mensaje = f"❌ Error procesando el acta: {e}"

    return index(mensaje)
if __name__ == '__main__':
    app.run(debug=True)

