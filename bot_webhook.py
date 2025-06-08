#!/usr/bin/env python3
"""
Planeador-Aula-Rick-Bot - VERSIÓN WEBHOOK PARA RENDER WEB SERVICE
- Convertido de worker a web service
- Usa webhooks en lugar de long-polling
- Mantiene toda la funcionalidad original
- Optimizado para costos en Render
"""

import asyncio
import json
import os
import logging
import tempfile
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import threading

# Debug: Mostrar variables de entorno disponibles
print("🔍 DEBUG: Variables de entorno disponibles:")
print(f"TELEGRAM_BOT_TOKEN existe: {'TELEGRAM_BOT_TOKEN' in os.environ}")
print(f"GOOGLE_API_KEY existe: {'GOOGLE_API_KEY' in os.environ}")

# Cargar variables de entorno
load_dotenv()

# Debug: Intentar leer variables después de load_dotenv
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
google_api_key = os.getenv("GOOGLE_API_KEY")

print(f"🔍 TELEGRAM_BOT_TOKEN cargado: {bool(telegram_token)}")
print(f"🔍 GOOGLE_API_KEY cargado: {bool(google_api_key)}")

# Fallback: Si no se cargan desde env, usar valores directos (TEMPORAL)
if not telegram_token:
    telegram_token = "7808524240:AAGFNv5-CgvmH-EmWo8TaNJDjGS-XyKFrzk"
    print("⚠️ Usando TELEGRAM_BOT_TOKEN fallback")

if not google_api_key:
    google_api_key = "AIzaSyBWYoY_WgiBd6_p0q7tvaVvV8Qzd3rUVQ0"
    print("⚠️ Usando GOOGLE_API_KEY fallback")

# Imports para Telegram
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import io
import requests

# Imports para IA con búsqueda web
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except:
    GEMINI_AVAILABLE = False

# Imports para generación de archivos
import pandas as pd
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear aplicación Flask
app = Flask(__name__)

class PlaneadorConAudio:
    def __init__(self):
        self.user_sessions = {}
        self.estandares_men = self._load_estandares_men()
        
        # Configurar Gemini con API key
        if google_api_key and GEMINI_AVAILABLE:
            try:
                genai.configure(api_key=google_api_key)
                self.model = genai.GenerativeModel('gemini-2.0-flash')
                self.search_model = genai.GenerativeModel('gemini-2.0-flash')
                logger.info("✅ Gemini AI con transcripción de audio disponible")
            except Exception as e:
                logger.error(f"Error configurando Gemini: {e}")
                self.model = None
                self.search_model = None
        else:
            self.model = None
            self.search_model = None
            logger.warning("⚠️ Gemini AI no disponible - verifique GOOGLE_API_KEY")
    
    def _load_estandares_men(self) -> str:
        """Carga estándares completos del MEN desde el archivo extraído"""
        try:
            # Buscar el archivo en diferentes ubicaciones
            possible_paths = [
                '/workspace/estandares_men_detailed.txt',
                './estandares_men_detailed.txt',
                'estandares_men_detailed.txt'
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        return f.read()
        except Exception as e:
            logger.warning(f"No se pudo cargar estándares del MEN: {e}")
        
        # Fallback con algunos estándares básicos
        return """
        ESTÁNDARES MEN BÁSICOS:
        MATEMÁTICAS:
        - Pensamiento Numérico: Resuelvo y formulo problemas con números naturales
        - Pensamiento Algebraico: Utilizo técnicas algebraicas para resolver problemas
        - Pensamiento Geométrico: Reconozco propiedades de figuras geométricas
        
        ESPAÑOL:
        - Comprensión lectora: Leo diversos tipos de texto
        - Producción textual: Produzco textos escritos coherentes
        
        CIENCIAS NATURALES:
        - Pensamiento científico: Explico fenómenos del mundo natural
        """
    
    def get_user_session(self, user_id: int) -> Dict:
        """Obtiene o crea sesión de usuario"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                "conversation_history": [],
                "data": {
                    "asignatura": None,
                    "temas": [],
                    "grado": None,
                    "año": datetime.now().year
                },
                "context": "inicio",
                "current_tema": {
                    "tema": None,
                    "periodo": None,
                    "fechas": None
                }
            }
        return self.user_sessions[user_id]
    
    def reset_session(self, user_id: int):
        """Resetea completamente la sesión del usuario"""
        self.user_sessions[user_id] = {
            "conversation_history": [],
            "data": {
                "asignatura": None,
                "temas": [],
                "grado": None,
                "año": datetime.now().year
            },
            "context": "inicio",
            "current_tema": {
                "tema": None,
                "periodo": None,
                "fechas": None
            }
        }
        logger.info(f"🔄 Sesión reseteada para usuario {user_id}")
    
    async def classify_message_intent(self, message: str) -> str:
        """Usa Gemini para clasificar la intención del mensaje"""
        if not self.model:
            return "planeador"
        
        prompt = f"""
Clasifica la intención del siguiente mensaje del usuario en UNA de estas categorías:

1. "saludo_nuevo" - Si es un saludo o dice que quiere crear un NUEVO plan de aula
2. "planeador" - Si está proporcionando información para un plan de aula (asignatura, grado, tema, período, fechas)
3. "continuar_planeador" - Si dice sí/no para continuar con el planeador
4. "consulta_general" - Si hace una pregunta general no relacionada con planeadores

MENSAJE: "{message}"

Responde SOLO con una de las 4 palabras: saludo_nuevo, planeador, continuar_planeador, consulta_general
"""

        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            intent = response.text.strip().lower()
            
            # Validar respuesta
            valid_intents = ["saludo_nuevo", "planeador", "continuar_planeador", "consulta_general"]
            if intent in valid_intents:
                return intent
            else:
                return "planeador"  # Default
                
        except Exception as e:
            logger.error(f"Error clasificando intención: {e}")
            return "planeador"
    
    async def transcribe_audio_with_ai(self, audio_data: bytes) -> str:
        """Transcribe audio usando Gemini"""
        if not self.model:
            return "Error: IA no disponible para transcripción"
        
        try:
            # Crear un prompt para transcripción
            prompt = """
Transcribe el siguiente audio en español. 
Devuelve SOLO el texto transcrito, sin explicaciones adicionales.
Si el audio contiene información sobre planeadores de aula (asignatura, grado, tema, período, fechas), transcríbelo exactamente como se dice.
"""
            
            # Generar contenido con el audio
            response = await asyncio.to_thread(
                self.model.generate_content, 
                [prompt, {"mime_type": "audio/ogg", "data": audio_data}]
            )
            
            transcribed_text = response.text.strip()
            logger.info(f"🎤 Audio transcrito: {transcribed_text[:100]}...")
            return transcribed_text
            
        except Exception as e:
            logger.error(f"Error transcribiendo audio: {e}")
            return "Lo siento, no pude transcribir el audio. Por favor, envía un mensaje de texto."

    async def handle_general_query(self, message: str) -> str:
        """Maneja consultas generales fuera del dominio de planeadores"""
        if not self.model:
            return "Lo siento, mi especialidad es la generación de planeadores de aula."
        
        prompt = f"""
Eres un asistente especializado en planeadores de aula. El usuario te hizo una pregunta que NO está relacionada con planeadores.

Responde de manera BREVE (máximo 2-3 líneas) la pregunta, pero al final SIEMPRE menciona que tu especialidad es la generación de planeadores de aula.

PREGUNTA: "{message}"

Respuesta breve + recordatorio de especialidad:
"""

        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Error respondiendo consulta general: {e}")
            return "Lo siento, mi especialidad es la generación de planeadores de aula. ¿Te ayudo a crear un plan?"
    
    async def extract_info_with_ai(self, message: str, session: Dict) -> Dict:
        """Usa IA para extraer información del mensaje"""
        if not self.model:
            return {"error": "IA no disponible"}
        
        # Contexto actual
        current_data = session['data']
        current_tema = session['current_tema']
        
        # Prompt para el modelo
        prompt = f"""
Eres un asistente que extrae información educativa de mensajes.

DATOS ACTUALES:
- Asignatura: {current_data.get('asignatura', 'No definida')}
- Grado: {current_data.get('grado', 'No definido')}
- Temas anteriores: {len(current_data.get('temas', []))}

TEMA ACTUAL EN CONSTRUCCIÓN:
- Tema: {current_tema.get('tema', 'No definido')}
- Período: {current_tema.get('periodo', 'No definido')}
- Fechas: {current_tema.get('fechas', 'No definidas')}

MENSAJE DEL USUARIO: "{message}"

EXTRAE Y FORMATEA la información disponible en el mensaje. Responde ÚNICAMENTE con un JSON válido con esta estructura:

{{
    "asignatura": "nombre de la asignatura si está presente (ej: Matemáticas, Español, Ciencias Naturales) o null",
    "grado": "grado en formato X-Y (ej: 8-1, 6-1, 7-1) o null",
    "tema": "nombre del tema específico o null",
    "periodo": "número del período (1, 2, 3, 4) o null",
    "fechas": "fechas en formato 'dia de mes - dia de mes' (ej: '7 de mayo - 30 de junio') o null"
}}

REGLAS IMPORTANTES:
1. Solo extrae información que esté EXPLÍCITAMENTE presente
2. Si no hay información de un campo, usa null
3. Para grado: convierte texto a formato X-1 (ej: "octavo" → "8-1", "grado 6" → "6-1")
4. Para fechas: normaliza a formato estándar (ej: "siete de mayo al 30 de junio" → "7 de mayo - 30 de junio")
5. Para período: convierte texto a número (ej: "tercer periodo" → 3)
6. NO inventes información que no esté en el mensaje
7. Responde SOLO el JSON, sin explicaciones adicionales
"""

        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            
            # Limpiar respuesta y extraer JSON
            json_text = response.text.strip()
            
            # Remover markdown si existe
            if json_text.startswith('```json'):
                json_text = json_text[7:]
            if json_text.endswith('```'):
                json_text = json_text[:-3]
            
            json_text = json_text.strip()
            
            # Parsear JSON
            extracted_data = json.loads(json_text)
            
            logger.info(f"🤖 IA extrajo: {extracted_data}")
            return extracted_data
            
        except Exception as e:
            logger.error(f"Error en extracción con IA: {e}")
            return {"error": str(e)}
    
    async def search_standards_with_ai(self, tema: str, asignatura: str, grado: str) -> Dict[str, str]:
        """Busca estándares usando IA - primero en PDF del MEN, luego en Internet"""
        if not self.model:
            return {
                'estandar': 'Estándar no disponible (IA no funcional)',
                'tipo_pensamiento': 'general'
            }
        
        # Paso 1: Buscar en estándares del MEN
        prompt_men = f"""
Analiza cuidadosamente los siguientes estándares del Ministerio de Educación de Colombia y encuentra el estándar y tipo de pensamiento más apropiado para:

TEMA: {tema}
ASIGNATURA: {asignatura}  
GRADO: {grado}

ESTÁNDARES DEL MEN:
{self.estandares_men[:4000]}

Busca coincidencias por:
1. Tema específico (ej: "números naturales", "comprensión lectora", "productos notables")
2. Asignatura (Matemáticas, Español/Lenguaje, Ciencias Naturales)
3. Grado correspondiente

Responde ÚNICAMENTE con JSON válido:
{{"estandar": "estándar encontrado", "tipo_pensamiento": "tipo", "encontrado_en_men": true}}

Si NO encuentras nada específico, responde:
{{"estandar": null, "tipo_pensamiento": null, "encontrado_en_men": false}}
"""

        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt_men)
            json_text = response.text.strip()
            
            # Limpiar JSON
            if json_text.startswith('```json'):
                json_text = json_text[7:]
            if json_text.endswith('```'):
                json_text = json_text[:-3]
            
            result = json.loads(json_text.strip())
            
            # Si encontró en MEN, retornar
            if result.get('encontrado_en_men') and result.get('estandar'):
                logger.info(f"📚 Estándar encontrado en MEN: {result['estandar'][:50]}...")
                return {
                    'estandar': result['estandar'],
                    'tipo_pensamiento': result['tipo_pensamiento'] or 'general'
                }
                
        except Exception as e:
            logger.warning(f"Error buscando en estándares MEN: {e}")
        
        # Paso 2: Buscar en Internet con búsqueda web 
        try:
            prompt_web = f"""
Basándote en los estándares curriculares del Ministerio de Educación de Colombia, proporciona el estándar y tipo de pensamiento más apropiado para:

TEMA: {tema}
ASIGNATURA: {asignatura}
GRADO: {grado}

Responde ÚNICAMENTE con el siguiente formato JSON válido (sin explicaciones adicionales):

{{"estandar": "texto del estándar curricular", "tipo_pensamiento": "tipo de pensamiento"}}
"""

            # Usar modelo con búsqueda web
            response = await asyncio.to_thread(self.search_model.generate_content, prompt_web)
            json_text = response.text.strip()
            
            # Extraer JSON de manera más robusta
            if '```json' in json_text:
                json_start = json_text.find('```json') + 7
                json_end = json_text.find('```', json_start)
                json_text = json_text[json_start:json_end]
            elif '{' in json_text and '}' in json_text:
                json_start = json_text.find('{')
                json_end = json_text.rfind('}') + 1
                json_text = json_text[json_start:json_end]
            
            web_result = json.loads(json_text.strip())
            
            logger.info(f"🌐 Estándar encontrado en web: {web_result.get('estandar', '')[:50]}...")
            
            return {
                'estandar': web_result.get('estandar', 'Estándar pendiente de asignar según currículo institucional'),
                'tipo_pensamiento': web_result.get('tipo_pensamiento', 'general')
            }
            
        except Exception as e:
            logger.warning(f"Error buscando estándares en web: {e}")
            
        # Fallback
        return {
            'estandar': f'Estándar curricular de {asignatura} para {tema} en {grado} - Pendiente de verificación institucional',
            'tipo_pensamiento': 'general'
        }
    
    def is_current_tema_complete(self, current_tema: Dict) -> bool:
        """Verifica si el tema actual está completo"""
        return all([
            current_tema.get('tema'),
            current_tema.get('periodo'),
            current_tema.get('fechas')
        ])
    
    def is_ready_to_generate(self, data: Dict) -> bool:
        """Verifica si está listo para generar el plan"""
        return all([
            data.get('asignatura'),
            data.get('temas'),
            data.get('grado')
        ])
    
    def get_missing_info_for_tema(self, current_tema: Dict) -> List[str]:
        """Retorna información faltante para el tema actual"""
        missing = []
        if not current_tema.get('tema'):
            missing.append("tema")
        if not current_tema.get('periodo'):
            missing.append("período")
        if not current_tema.get('fechas'):
            missing.append("fechas")
        return missing
    
    def get_missing_info_general(self, data: Dict) -> List[str]:
        """Retorna información general faltante"""
        missing = []
        if not data.get('asignatura'):
            missing.append("asignatura")
        if not data.get('grado'):
            missing.append("grado")
        return missing
    
    async def generate_plan_data(self, data: Dict) -> List[Dict]:
        """Genera datos del plan con estándares reales del MEN o Internet"""
        plan_data = []
        
        estrategias_pedagogicas = [
            "Exploración de presaberes.",
            "Dinámicas en clases.", 
            "Aplicación de las guías de clase.",
            "Modelación y ejemplificación.",
            "Resolución de situaciones contextuales."
        ]
        
        recursos = [
            "Guías de clase.",
            'Texto guía "caminos del saber" y "Aulas sin frontera".',
            "Plan de área.",
            "Estándares de competencias del MEN.",
            "**proferick.com (página con IA)**.",
            "Equipamiento de aula (tablero, marcadores, calculadoras)."
        ]
        
        evaluacion = [
            "Trabajo individual y desarrollo de la guía de clase.",
            "Evaluaciones cortas semanales.",
            "Evaluaciones finales de periodo.",
            "Actividades en clase participativas.",
            "Proyectos de aplicación práctica."
        ]
        
        for tema_info in data['temas']:
            # Buscar estándar real usando IA
            estandar_info = await self.search_standards_with_ai(
                tema_info['tema'], 
                data['asignatura'], 
                data['grado']
            )
            
            plan_data.append({
                'Asignatura': data['asignatura'],
                'Grado': data['grado'],
                'Periodo': tema_info['periodo'],
                'Tema': tema_info['tema'],
                'Estándar': estandar_info['estandar'],
                'TipoPensamiento': estandar_info['tipo_pensamiento'],
                'Fechas': tema_info['fechas'],
                'EstrategiasPedagogicas': '\n'.join([f"• {est}" for est in estrategias_pedagogicas]),
                'Recursos': '\n'.join([f"• {rec}" for rec in recursos]),
                'Evaluacion': '\n'.join([f"• {eva}" for eva in evaluacion]),
                'Año': data['año']
            })
        
        return plan_data
    
    def generate_excel(self, plan_data: List[Dict], user_id: int) -> str:
        """Genera archivo Excel"""
        os.makedirs("/tmp/output", exist_ok=True)
        filename = f"/tmp/output/plan_aula_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        df = pd.DataFrame(plan_data)
        
        columnas_ordenadas = [
            'Asignatura', 'Grado', 'Periodo', 'Tema', 'Estándar', 'TipoPensamiento', 
            'Fechas', 'EstrategiasPedagogicas', 'Recursos', 'Evaluacion', 'Año'
        ]
        
        df = df[columnas_ordenadas]
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Plan de Aula', index=False)
            
            worksheet = writer.sheets['Plan de Aula']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 5, 60)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        return filename
    
    def generate_pdf(self, plan_data: List[Dict], user_id: int) -> str:
        """Genera archivo PDF profesional con ajuste automático de texto"""
        os.makedirs("/tmp/output", exist_ok=True)
        filename = f"/tmp/output/plan_aula_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        doc = SimpleDocTemplate(filename, pagesize=landscape(A4), 
                              rightMargin=1*cm, leftMargin=1*cm,
                              topMargin=2*cm, bottomMargin=2*cm)
        
        story = []
        styles = getSampleStyleSheet()
        
        header_style = ParagraphStyle(
            'CustomHeader',
            parent=styles['Normal'],
            fontSize=12,
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        # Estilo para celdas de la tabla
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontSize=7,
            leading=9,
            alignment=TA_CENTER,
            fontName='Helvetica',
            wordWrap='CJK'
        )
        
        # Estilo para celdas con mucho texto
        content_style = ParagraphStyle(
            'ContentStyle',
            parent=styles['Normal'],
            fontSize=6,
            leading=8,
            alignment=TA_LEFT,
            fontName='Helvetica',
            wordWrap='CJK',
            leftIndent=2,
            rightIndent=2
        )
        
        # Agregar escudo si existe
        try:
            if os.path.exists('./escudo_colegio.jpg'):
                escudo = Image('./escudo_colegio.jpg', width=2*cm, height=2*cm)
                story.append(escudo)
                story.append(Spacer(1, 0.5*cm))
            elif os.path.exists('/workspace/escudo_colegio.jpg'):
                escudo = Image('/workspace/escudo_colegio.jpg', width=2*cm, height=2*cm)
                story.append(escudo)
                story.append(Spacer(1, 0.5*cm))
        except Exception as e:
            logger.warning(f"No se pudo cargar el escudo: {e}")
        
        # Encabezado institucional
        header_text = """
        <para align=center><b>INSTITUCIÓN EDUCATIVA COLEGIO GILBERTO CLARO LOZANO</b><br/>
        Resolución No. 003477 del 11 noviembre de 2020<br/>
        DANE: 254398000724 NIT: 807006133-6<br/>
        "Querer es Poder"<br/>
        La Playa - KDK D 1 200 CORREGIMIENTO ASPASICA<br/>
        <br/>
        <b>PLANEADOR DE CLASES</b>
        </para>
        """
        
        header = Paragraph(header_text, header_style)
        story.append(header)
        story.append(Spacer(1, 1*cm))
        
        # Crear encabezados como Paragraphs
        headers = [
            Paragraph('<b>Asignatura</b>', cell_style),
            Paragraph('<b>Grado</b>', cell_style),
            Paragraph('<b>Periodo</b>', cell_style),
            Paragraph('<b>Tema</b>', cell_style),
            Paragraph('<b>Estándar</b>', cell_style),
            Paragraph('<b>Tipo<br/>Pensamiento</b>', cell_style),
            Paragraph('<b>Fechas</b>', cell_style),
            Paragraph('<b>Estrategias<br/>Pedagógicas</b>', cell_style),
            Paragraph('<b>Recursos</b>', cell_style),
            Paragraph('<b>Evaluación</b>', cell_style)
        ]
        
        table_data = [headers]
        
        # Procesar filas de datos
        for row in plan_data:
            # Crear cada celda como Paragraph para ajuste automático
            row_data = [
                Paragraph(str(row['Asignatura']), cell_style),
                Paragraph(str(row['Grado']), cell_style),
                Paragraph(str(row['Periodo']), cell_style),
                Paragraph(str(row['Tema']), cell_style),
                Paragraph(str(row['Estándar']), content_style),
                Paragraph(str(row['TipoPensamiento']), cell_style),
                Paragraph(str(row['Fechas']), cell_style),
                Paragraph(str(row['EstrategiasPedagogicas']).replace('•', '•<br/>'), content_style),
                Paragraph(str(row['Recursos']).replace('•', '•<br/>'), content_style),
                Paragraph(str(row['Evaluacion']).replace('•', '•<br/>'), content_style)
            ]
            table_data.append(row_data)
        
        # Crear tabla con anchos ajustados
        table = Table(table_data, colWidths=[
            2*cm, 1.5*cm, 1.5*cm, 2.5*cm, 4*cm, 2*cm, 
            2.5*cm, 3.5*cm, 3.5*cm, 3.5*cm
        ])
        
        # Estilo de tabla mejorado para ajuste automático
        table.setStyle(TableStyle([
            # Encabezados
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            
            # Contenido
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('ALIGN', (0, 1), (3, -1), 'CENTER'),  # Primeras 4 columnas centradas
            ('ALIGN', (4, 1), (-1, -1), 'LEFT'),   # Últimas columnas alineadas a la izquierda
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),   # Alineación vertical superior
            
            # Bordes
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.black),
            
            # Padding ajustado
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            
            # Ajuste automático de altura
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
        ]))
        
        story.append(table)
        story.append(Spacer(1, 1*cm))
        
        footer_text = f"""
        <para align=center>
        <b>Aprobado por:</b> Mg. MARCO ANTONIO JAMES GARCÍA<br/>
        <b>Generado por:</b> Planeador-Aula-Rick-Bot con IA Gemini<br/>
        <b>Fecha:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>
        Página 1/1
        </para>
        """
        
        footer = Paragraph(footer_text, styles['Normal'])
        story.append(footer)
        
        doc.build(story)
        return filename
    
    async def process_message(self, message: str, user_id: int) -> Dict[str, Any]:
        """Procesa mensaje del usuario con IA completa"""
        session = self.get_user_session(user_id)
        
        # Agregar a historial
        session['conversation_history'].append({
            "user": message,
            "timestamp": datetime.now().isoformat()
        })
        
        # Clasificar intención del mensaje
        intent = await self.classify_message_intent(message)
        logger.info(f"🎯 Intención detectada: {intent}")
        
        # Manejar según intención
        if intent == "saludo_nuevo":
            # Resetear sesión completamente
            self.reset_session(user_id)
            session = self.get_user_session(user_id)
            
            response = "👋 ¡Hola! Soy **Planeador-Aula-Rick-Bot CON AUDIO**.\n\n"
            response += "🤖 Uso inteligencia artificial Gemini para:\n"
            response += "• 📚 **Extraer estándares del PDF del MEN**\n"
            response += "• 🌐 **Buscar estándares en Internet** si no los encuentro\n"
            response += "• 🎤 **Transcribir audio** y procesarlo como texto\n"
            response += "• 🧠 **Entender cualquier formato** de entrada natural\n"
            response += "• 📋 **Generar PDF y Excel** profesionales\n\n"
            response += "💬 Puedes enviarme:\n"
            response += "• **Texto**: \"Matemáticas, productos notables, grado 8, período 3, mayo-junio\"\n"
            response += "• **Audio**: Grabación de voz con la misma información\n\n"
            response += "¿Qué plan de aula necesitas crear?"
            
            return {
                "telegram_response": response,
                "completed": False
            }
        
        elif intent == "consulta_general":
            # Manejar consulta fuera del dominio
            response = await self.handle_general_query(message)
            return {
                "telegram_response": response,
                "completed": False
            }
        
        elif intent == "continuar_planeador":
            if message.lower().strip() in ['si', 'sí', 'yes', 'ok', 'vale', 'claro']:
                session['current_tema'] = {
                    "tema": None,
                    "periodo": None,
                    "fechas": None
                }
                response = "📝 Perfecto! Vamos a agregar otro tema.\n\n"
                response += "Dime el **tema**, **período** y **fechas** por texto o audio."
                
                return {
                    "telegram_response": response,
                    "completed": False
                }
            
            elif message.lower().strip() in ['no', 'nope', 'listo', 'ya', 'generar', 'crear']:
                if self.is_ready_to_generate(session['data']):
                    plan_data = await self.generate_plan_data(session['data'])
                    
                    try:
                        excel_path = self.generate_excel(plan_data, user_id)
                        pdf_path = self.generate_pdf(plan_data, user_id)
                        
                        response = "✅ ¡Plan de aula generado exitosamente!\n\n"
                        response += "📋 **Resumen del plan:**\n"
                        response += f"• **Asignatura:** {session['data']['asignatura']}\n"
                        response += f"• **Grado:** {session['data']['grado']}\n"
                        response += f"• **Total de temas:** {len(session['data']['temas'])}\n"
                        
                        for i, tema in enumerate(session['data']['temas'], 1):
                            response += f"  {i}. {tema['tema']} (Período {tema['periodo']}, {tema['fechas']})\n"
                        
                        response += "\n📁 Te envío los archivos generados.\n"
                        response += "🎯 **Estándares extraídos del MEN/Internet con IA**\n"
                        response += "💡 **Incluye proferick.com en recursos**\n\n"
                        response += "🔄 Para crear otro plan, escribe 'Hola' o envía audio"
                        
                        return {
                            "telegram_response": response,
                            "files_to_send": [
                                {"path": pdf_path, "caption": "Plan de aula con estándares MEN y proferick.com"},
                                {"path": excel_path, "caption": "Plan de aula editable"}
                            ],
                            "completed": True
                        }
                        
                    except Exception as e:
                        logger.error(f"Error generando archivos: {e}")
                        return {
                            "telegram_response": f"⚠️ Error generando archivos: {str(e)}",
                            "completed": True
                        }
                else:
                    return {
                        "telegram_response": "⚠️ Aún falta información para generar el plan.",
                        "completed": False
                    }
        
        # Procesar como información del planeador
        extracted = await self.extract_info_with_ai(message, session)
        
        if "error" in extracted:
            return {
                "telegram_response": f"⚠️ Error procesando mensaje: {extracted['error']}",
                "completed": False
            }
        
        # Actualizar sesión solo con campos no vacíos
        for key, value in extracted.items():
            if value is not None:
                if key in ['asignatura', 'grado']:
                    session['data'][key] = value
                elif key in ['tema', 'periodo', 'fechas']:
                    session['current_tema'][key] = value
        
        logger.info(f"📊 Datos: {session['data']}")
        logger.info(f"📊 Tema actual: {session['current_tema']}")
        
        # Verificar si el tema actual está completo
        if self.is_current_tema_complete(session['current_tema']):
            tema_completo = session['current_tema'].copy()
            session['data']['temas'].append(tema_completo)
            
            session['current_tema'] = {
                "tema": None,
                "periodo": None,
                "fechas": None
            }
            
            response = f"✅ **Tema agregado exitosamente:**\n"
            response += f"• **Tema:** {tema_completo['tema']}\n"
            response += f"• **Período:** {tema_completo['periodo']}\n"
            response += f"• **Fechas:** {tema_completo['fechas']}\n\n"
            response += "❓ **¿Quieres agregar otro tema en otras fechas?**\n"
            response += "Responde **'Sí'** para agregar otro tema o **'No'** para generar el plan."
            
            return {
                "telegram_response": response,
                "completed": False
            }
        
        # Si no está completo, mostrar información y solicitar faltante
        missing_general = self.get_missing_info_general(session['data'])
        missing_tema = self.get_missing_info_for_tema(session['current_tema'])
        
        response = ""
        
        # Mostrar información recolectada
        if session['data']['asignatura'] or session['data']['grado'] or session['data']['temas']:
            response += "📝 **Información recolectada:**\n"
            if session['data']['asignatura']:
                response += f"✅ Asignatura: {session['data']['asignatura']}\n"
            if session['data']['grado']:
                response += f"✅ Grado: {session['data']['grado']}\n"
            if session['data']['temas']:
                response += f"✅ Temas anteriores: {len(session['data']['temas'])}\n"
            response += "\n"
        
        # Mostrar información del tema actual
        if any(session['current_tema'].values()):
            response += "📝 **Tema actual:**\n"
            if session['current_tema']['tema']:
                response += f"✅ Tema: {session['current_tema']['tema']}\n"
            if session['current_tema']['periodo']:
                response += f"✅ Período: {session['current_tema']['periodo']}\n"
            if session['current_tema']['fechas']:
                response += f"✅ Fechas: {session['current_tema']['fechas']}\n"
            response += "\n"
        
        # Solicitar información faltante
        all_missing = missing_general + missing_tema
        if all_missing:
            response += f"🔍 **Falta:** {', '.join(all_missing)}\n\n"
            response += "Por favor proporciona la información faltante por texto o audio."
        else:
            response += "✅ ¡Información completa!"
        
        return {
            "telegram_response": response,
            "completed": False
        }

# Instancia global del bot
bot_instance = PlaneadorConAudio()

# Crear bot de Telegram para envío de mensajes
telegram_bot = Bot(token=telegram_token)

# Función para enviar mensaje de texto
async def send_telegram_message(chat_id: int, text: str):
    """Envía mensaje de texto a Telegram"""
    try:
        await telegram_bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        logger.info(f"✅ Mensaje enviado a {chat_id}")
    except Exception as e:
        logger.error(f"❌ Error enviando mensaje: {e}")

# Función para enviar archivo
async def send_telegram_document(chat_id: int, file_path: str, caption: str):
    """Envía documento a Telegram"""
    try:
        with open(file_path, 'rb') as file:
            await telegram_bot.send_document(
                chat_id=chat_id,
                document=file,
                caption=caption
            )
        logger.info(f"✅ Archivo enviado a {chat_id}: {file_path}")
    except Exception as e:
        logger.error(f"❌ Error enviando archivo: {e}")

# Función para procesar audio
async def process_telegram_audio(file_id: str) -> bytes:
    """Descarga y procesa audio de Telegram"""
    try:
        file = await telegram_bot.get_file(file_id)
        file_url = file.file_path
        
        # Descargar archivo
        response = requests.get(f"https://api.telegram.org/file/bot{telegram_token}/{file_url}" )
        return response.content
    except Exception as e:
        logger.error(f"❌ Error procesando audio: {e}")
        return b""

# Rutas Flask
@app.route('/', methods=['GET'])
def home():
    """Página de inicio del bot"""
    return jsonify({
        "status": "active",
        "bot": "Planeador-Aula-Rick-Bot",
        "version": "webhook",
        "description": "Bot para generar planeadores de aula con IA",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health"
        }
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check para Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "gemini_available": GEMINI_AVAILABLE
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint principal para recibir webhooks de Telegram"""
    try:
        # Obtener datos del webhook
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data received"}), 400
        
        logger.info(f"📨 Webhook recibido: {data}")
        
        # Procesar en hilo separado para no bloquear
        threading.Thread(target=process_webhook_async, args=(data,)).start()
        
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"❌ Error en webhook: {e}")
        return jsonify({"error": str(e)}), 500

def process_webhook_async(data):
    """Procesa webhook de forma asíncrona"""
    try:
        # Crear nuevo loop para este hilo
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Ejecutar procesamiento
        loop.run_until_complete(process_webhook_data(data))
        
    except Exception as e:
        logger.error(f"❌ Error procesando webhook async: {e}")
    finally:
        loop.close()

async def process_webhook_data(data):
    """Procesa los datos del webhook"""
    try:
        # Verificar si hay mensaje
        if 'message' not in data:
            return
        
        message_data = data['message']
        chat_id = message_data['chat']['id']
        user_id = message_data['from']['id']
        
        # Procesar según tipo de mensaje
        if 'text' in message_data:
            # Mensaje de texto
            text = message_data['text']
            
            # Comando /start
            if text == '/start':
                text = "Hola"
            
            # Procesar mensaje
            result = await bot_instance.process_message(text, user_id)
            
            # Enviar respuesta
            await send_telegram_message(chat_id, result["telegram_response"])
            
            # Enviar archivos si los hay
            for file_info in result.get("files_to_send", []):
                if os.path.exists(file_info["path"]):
                    await send_telegram_document(
                        chat_id, 
                        file_info["path"], 
                        file_info["caption"]
                    )
        
        elif 'voice' in message_data:
            # Mensaje de audio
            voice_data = message_data['voice']
            file_id = voice_data['file_id']
            
            # Notificar que está procesando
            await send_telegram_message(chat_id, "🎤 Transcribiendo audio...")
            
            # Descargar y procesar audio
            audio_data = await process_telegram_audio(file_id)
            
            if audio_data:
                # Transcribir audio
                transcribed_text = await bot_instance.transcribe_audio_with_ai(audio_data)
                
                if transcribed_text and "Error:" not in transcribed_text:
                    # Mostrar transcripción
                    await send_telegram_message(chat_id, f"📝 **Transcripción:** {transcribed_text}")
                    
                    # Procesar como mensaje de texto
                    result = await bot_instance.process_message(transcribed_text, user_id)
                    
                    # Enviar respuesta
                    await send_telegram_message(chat_id, result["telegram_response"])
                    
                    # Enviar archivos si los hay
                    for file_info in result.get("files_to_send", []):
                        if os.path.exists(file_info["path"]):
                            await send_telegram_document(
                                chat_id, 
                                file_info["path"], 
                                file_info["caption"]
                            )
                else:
                    await send_telegram_message(chat_id, transcribed_text)
            else:
                await send_telegram_message(chat_id, "❌ Error procesando audio. Por favor, envía un mensaje de texto.")
        
    except Exception as e:
        logger.error(f"❌ Error procesando datos del webhook: {e}")

# Función para configurar webhook
async def setup_webhook():
    """Configura el webhook de Telegram"""
    try:
        webhook_url = os.getenv('WEBHOOK_URL')
        if not webhook_url:
            logger.warning("⚠️ WEBHOOK_URL no configurada. El webhook debe configurarse manualmente.")
            return
        
        # Configurar webhook
        await telegram_bot.set_webhook(url=f"{webhook_url}/webhook")
        logger.info(f"✅ Webhook configurado: {webhook_url}/webhook")
        
    except Exception as e:
        logger.error(f"❌ Error configurando webhook: {e}")

if __name__ == '__main__':
    # Verificar token
    if not telegram_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN no encontrado")
        exit(1)
    
    logger.info("🚀 Iniciando Planeador-Aula-Rick-Bot (Webhook)")
    logger.info(f"🤖 Gemini AI disponible: {GEMINI_AVAILABLE}")
    
    # Comentado: La configuración del webhook se hace una sola vez desde el script setup_webhook.py
    # if os.getenv('WEBHOOK_URL'):
    #     asyncio.run(setup_webhook())
    
    # Iniciar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)


