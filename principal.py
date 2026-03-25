import os
import sys
import json
import hmac
import hashlib
import requests
import psycopg2
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel, QTextEdit
from pynput import keyboard
import time

# Carrega variáveis do arquivo .env
load_dotenv()

# --- CAPTURA DE TECLADO GLOBAL (FUNCIOA SEM FOCO) ---
class OuvinteGlobal(QObject):
    serial_capturado = Signal(str)

    def __init__(self):
        super().__init__()
        self.buffer = ""
        self.tempos = [] # Guarda o momento de cada tecla
        self.timer_limpeza = QTimer()
        self.timer_limpeza.setSingleShot(True)
        self.timer_limpeza.timeout.connect(self.limpar_buffer)
        
        self.listener = keyboard.Listener(on_press=self.ao_pressionar)
        self.listener.start()

    def ao_pressionar(self, tecla):
        try:
            if hasattr(tecla, 'char') and tecla.char is not None:
                char = tecla.char
                if char.isalnum():
                    self.buffer += char
                    self.tempos.append(time.time())
                    self.timer_limpeza.start(100) # 100ms de tolerância entre teclas
            
            if tecla == keyboard.Key.enter:
                self.validar_rajada()
        except Exception:
            pass

    def validar_rajada(self):
        """
        Diferencia humano de máquina pela velocidade média.
        """
        if len(self.buffer) == 13:
            # Calcula o tempo total do primeiro ao último caractere
            tempo_total = self.tempos[-1] - self.tempos[0]
            
            if tempo_total < 0.5: # Limite de 500ms para 13 dígitos
                self.serial_capturado.emit(self.buffer)
            else:
                print(f"⚠️ Digitação humana detectada (Tempo: {tempo_total:.2f}s) - Descartando.")
        
        # Limpa tudo para a próxima tentativa
        self.limpar_buffer()

    def limpar_buffer(self):
        self.buffer = ""
        self.tempos = []

class SegurancaSette:
    @staticmethod
    def validar_serial(serial):
        """Garante que a leitura local tenha 13 dígitos[cite: 68]."""
        return bool(re.match(r'^\d{13}$', serial))

    @staticmethod
    def gerar_autenticacao(metodo, nome_funcao):
        """Geração de HMAC conforme manual SISGEM[cite: 23, 38]."""
        sistema = os.getenv("SPACECOM_SISTEMA", "sette") 
        chave_secreta = os.getenv("SPACECOM_CHAVE_API", "") 
        ruido = os.getenv("SPACECOM_RUIDO", "")
        
        # Formato de data: mês+hora+dia+minuto+ano em UTC [cite: 42, 43]
        agora_utc = datetime.now(timezone.utc)
        data_str = agora_utc.strftime("%m%H%d%M%Y")
        
        mensagem = f"{sistema.lower()}{data_str}{metodo.upper()}{ruido}{nome_funcao}"
        
        assinatura = hmac.new(
            chave_secreta.encode('utf-8'),
            mensagem.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return f"{sistema}:{assinatura}"

class GerenciadorPersistencia:
    def __init__(self):
        self.db_url = os.getenv("DB_URL")
        self.arquivo_txt = os.getenv("ARQUIVO_EMERGENCIA", "emergencia.txt")

    def registrar_log(self, dados_envio, resposta_api, sucesso_api):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            query = """
                INSERT INTO logs_producao 
                (serial, test_type, jiga_name, resultado, api_response_raw, enviado_api_externa, 
                 valor_estanqueidade, unidade_medida, programa_teste)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(query, (
                dados_envio['serial'], 
                dados_envio['tipo'], 
                dados_envio['jiga'], 
                'A' if sucesso_api else 'R',
                json.dumps(resposta_api),
                sucesso_api,
                dados_envio['valor_estanqueidade'],
                dados_envio['unidade_medida'],
                dados_envio['programa_teste']
            ))
            conn.commit()
            cur.close()
            conn.close()
            print(" GRAVADO NO POSTGRES")
        except Exception as e:
            print(f" ERRO BANCO: {e}")
            self.salvar_em_txt(dados_envio, resposta_api, e)

    def salvar_em_txt(self, dados, resposta, erro_db):
        with open(self.arquivo_txt, "a", encoding="utf-8") as f:
            f.write(f"DATA: {datetime.now()} | SERIAL: {dados['serial']} | ERRO: {erro_db} | RESPOSTA: {resposta}\n")

class ClienteApiSpacecom:
    def __init__(self):
        self.url_base = os.getenv("URL_BASE_SPACECOM")

    def enviar_estanqueidade(self, serial_completo):
        """Envia dados de estanqueidade para a API externa[cite: 59]."""
        endpoint = "/watertightness/log"
        auth = SegurancaSette.gerar_autenticacao("POST", "log")
        
        valor_mock = "30.5"
        unidade_mock = "Pa"
        prog_mock = "SETTE_V1"

        payload = {
            "serial": serial_completo[-10:], # API externa só aceita 10 dígitos
            "name_jiga": os.getenv("NOME_JIGA"),
            "info": {
                "Value": valor_mock, 
                "Status": "A", 
                "Value_unit": unidade_mock, 
                "Test_program": prog_mock, 
                "Failure_cause": ""
            }
        }
        
        try:
            res = requests.post(f"{self.url_base}{endpoint}", json=payload, headers={"Authorization": auth}, timeout=10)
            return res.json(), res.status_code == 200, valor_mock, unidade_mock, prog_mock
        except Exception as e:
            return {"erro": str(e)}, False, valor_mock, unidade_mock, prog_mock

# --- INTERFACE GRÁFICA ---
class InterfaceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SETTE - Integrador (Global)")
        self.setFixedSize(500, 350)
        
        self.api = ClienteApiSpacecom()
        self.dados = GerenciadorPersistencia()
        
        # Inicializa o ouvinte global
        self.ouvinte = OuvinteGlobal()
        self.ouvinte.serial_capturado.connect(self.validar_e_processar)

        self.configurar_ui()

    def configurar_ui(self):
        layout = QVBoxLayout()
        self.label_status = QLabel("MONITORANDO LEITOR (BACKGROUND ATIVO)")
        self.label_status.setStyleSheet("font-weight: bold; color: green;")
        
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet("background-color: black; color: #00FF00; font-family: Courier;")
        
        layout.addWidget(self.label_status)
        layout.addWidget(self.terminal)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def validar_e_processar(self, serial_recebido):
        serial = serial_recebido.strip()
        if SegurancaSette.validar_serial(serial):
            self.log_terminal(f"Serial Lido: {serial}")
            self.processar_envio(serial)
        else:
            self.log_terminal(f"Leitura ignorada (fora do padrão 13): {serial}")

    def processar_envio(self, serial):
        self.label_status.setText(" ENVIANDO DADOS...")
        
        # Chama API e recebe os valores Mock
        resposta, sucesso, v_est, v_uni, v_prog = self.api.enviar_estanqueidade(serial)

        # Prepara o dicionário para gravação no banco
        dados_log = {
            'serial': serial, 
            'tipo': 'estanque',
            'jiga': os.getenv("NOME_JIGA"), 
            'status': 'A' if sucesso else 'R',
            'valor_estanqueidade': v_est,
            'unidade_medida': v_uni,
            'programa_teste': v_prog
        }
        
        try:
            self.dados.registrar_log(dados_log, resposta, sucesso)
            
            if sucesso:
                self.label_status.setText(" SUCESSO NO ENVIO!")
                self.log_terminal(f"API OK: {resposta}")
            else:
                self.label_status.setText(" SALVO LOCAL (API REJEITOU)")
                self.log_terminal(f"API RESPOSTA: {resposta}")

        except Exception as e:
            self.label_status.setText(" ERRO NO BANCO DE DADOS")
            self.log_terminal(f"ERRO: {str(e)}")

    def log_terminal(self, msg):
        horario = datetime.now().strftime("%H:%M:%S")
        self.terminal.append(f"[{horario}] {msg}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = InterfaceApp()
    window.show()
    sys.exit(app.exec())