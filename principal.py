import os
import sys
import json
import hmac
import hashlib
import requests
import psycopg2
import re
import threading
from collections import deque
from queue import Queue, Empty
from datetime import datetime, timezone
from dotenv import load_dotenv
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLabel,
)
from pynput import keyboard
import time

from core.memoria import obter_memoria_operacional
from core.clp import ControladorCLP
from core.auto_memoria import CicloAutomaticoMemorias
from core.validacoes_memoria import AvaliadorCasosMemoria

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
                if char.isdigit():
                    self.buffer += char
                    self.tempos.append(time.time())
                    self.timer_limpeza.start(100) # 100ms de tolerância entre teclas
                    if len(self.buffer) == 10:
                        self.validar_rajada()
            
            if tecla == keyboard.Key.enter:
                self.validar_rajada()
        except Exception:
            pass

    def validar_rajada(self):
        """
        Diferencia humano de máquina pela velocidade média.
        """
        if len(self.buffer) == 10 and self.tempos:
            # Calcula o tempo total do primeiro ao último caractere
            tempo_total = self.tempos[-1] - self.tempos[0]
            
            if tempo_total < 0.5: # Limite de 500ms para 10 dígitos
                self.serial_capturado.emit(self.buffer)
            else:
                print(f" Digitação humana detectada (Tempo: {tempo_total:.2f}s) - Descartando.")
        
        # Limpa tudo para a próxima tentativa
        self.limpar_buffer()

    def limpar_buffer(self):
        self.buffer = ""
        self.tempos = []

class SegurancaSette:
    @staticmethod
    def validar_serial(serial):
        """Garante que a leitura local tenha 10 dígitos."""
        return bool(re.match(r'^\d{10}$', serial))

    @staticmethod
    def gerar_autenticacao(metodo, nome_funcao):
        """Geração de HMAC conforme manual SISGEM."""
        sistema = os.getenv("SPACECOM_SISTEMA", "sette") 
        chave_secreta = os.getenv("SPACECOM_CHAVE_API", "") 
        ruido = os.getenv("SPACECOM_RUIDO", "")
        
        # Formato de data: mês+hora+dia+minuto+ano em UTC
        agora_utc = datetime.now(timezone.utc)
        data_str = agora_utc.strftime("%m%H%d%M%Y")
        
        # Spacecom valida o método em minúsculo na string assinada.
        mensagem = f"{sistema.lower()}{data_str}{metodo.lower()}{ruido}{nome_funcao}"
        
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
                dados_envio.get('status', 'A' if sucesso_api else 'R'),
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


class FilaSerialFIFO:
    def __init__(self):
        self.itens = deque()

    def enfileirar(self, serial):
        self.itens.append(serial)
        print(f"[QUEUE] Serial enfileirado: {serial} | total={len(self.itens)}")

    def desenfileirar(self):
        if not self.itens:
            return None
        serial = self.itens.popleft()
        print(f"[QUEUE] Serial liberado para envio: {serial} | restante={len(self.itens)}")
        return serial

    def vazio(self):
        return not self.itens

class ClienteApiSpacecom:
    def __init__(self):
        self.url_base = os.getenv("URL_BASE_SPACECOM")

    def enviar_estanqueidade(self, serial_completo):
        """Envia dados de estanqueidade para a API externa."""
        endpoint = "/watertightness/log"
        auth = SegurancaSette.gerar_autenticacao("POST", "log")

        valor_envio = os.getenv("VALOR_ESTANQUEIDADE_PADRAO")
        unidade_envio = os.getenv("UNIDADE_ESTANQUEIDADE_PADRAO")
        programa_envio = os.getenv("PROGRAMA_TESTE_PADRAO")
        status_envio = os.getenv("STATUS_PADRAO", "A")

        if not all([valor_envio, unidade_envio, programa_envio, status_envio]):
            return {
                "erro": "Dados de envio incompletos no .env. Configure VALOR_ESTANQUEIDADE_PADRAO, UNIDADE_ESTANQUEIDADE_PADRAO, PROGRAMA_TESTE_PADRAO e STATUS_PADRAO."
            }, False, valor_envio, unidade_envio, programa_envio

        payload = {
            "serial": serial_completo[-10:],
            "name_jiga": os.getenv("NOME_JIGA"),
            "info": {
                "Value": valor_envio,
                "Status": status_envio,
                "Value_unit": unidade_envio,
                "Test_program": programa_envio,
                "Failure_cause": ""
            }
        }

        print(
            "[API] Preparando envio: "
            f"serial={payload['serial']} status={status_envio} "
            f"value={valor_envio} unit={unidade_envio} program={programa_envio}"
        )
        print(f"[API] Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        try:
            res = requests.post(f"{self.url_base}{endpoint}", json=payload, headers={"Authorization": auth}, timeout=10)
            try:
                corpo = res.json()
            except Exception:
                corpo = {"raw": res.text}

            print(f"[API] HTTP {res.status_code}")
            print(f"[API] Resposta: {json.dumps(corpo, ensure_ascii=False)}")
            return corpo, res.status_code == 200, valor_envio, unidade_envio, programa_envio
        except Exception as e:
            print(f"[API] Erro no envio: {e}")
            return {"erro": str(e)}, False, valor_envio, unidade_envio, programa_envio

# --- INTERFACE GRÁFICA ---
class InterfaceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SETTE - Integrador (Global)")
        self.setFixedSize(820, 520)
        self.memoria_operacional = obter_memoria_operacional()
        self.controlador_clp = ControladorCLP()
        self.ciclo_auto_memoria = CicloAutomaticoMemorias()
        self.avaliador_casos = AvaliadorCasosMemoria()
        self.memoria_auto_alvo = None
        
        self.api = ClienteApiSpacecom()
        self.dados = GerenciadorPersistencia()
        self.fila_serial = FilaSerialFIFO()
        self.processando = False
        self.fila_eventos = Queue()
        self.thread_worker = threading.Thread(target=self.processar_fila_worker, daemon=True)
        self.thread_worker.start()
        
        # Inicializa o ouvinte global
        self.ouvinte = OuvinteGlobal()
        self.ouvinte.serial_capturado.connect(self.validar_e_processar)

        self.configurar_ui()

    def configurar_ui(self):
        layout = QVBoxLayout()
        self.label_status = QLabel("MONITORANDO LEITOR (BACKGROUND ATIVO)")
        self.label_status.setStyleSheet("font-weight: bold; color: green;")

        self.label_clp = QLabel(
            f"CLP {self.controlador_clp.ip}:{self.controlador_clp.porta} | Protocolo: {self.controlador_clp.protocolo}"
        )
        self.label_clp.setStyleSheet("font-weight: bold; color: #1f4e79;")

        self.input_ip_clp = QLineEdit(self.controlador_clp.ip)
        self.input_ip_clp.setPlaceholderText("IP do CLP")

        self.input_porta_clp = QLineEdit(str(self.controlador_clp.porta))
        self.input_porta_clp.setPlaceholderText("Porta TCP")

        topo = QGridLayout()
        topo.addWidget(QLabel("IP CLP:"), 0, 0)
        topo.addWidget(self.input_ip_clp, 0, 1)
        topo.addWidget(QLabel("Porta:"), 0, 2)
        topo.addWidget(self.input_porta_clp, 0, 3)

        self.status_memoria = QLabel("Memorias prontas para teste: M130, M131, M132, M133")
        self.status_memoria.setStyleSheet("color: #444;")
        if self.ciclo_auto_memoria.habilitado():
            self.status_memoria.setText("Modo automatico de memorias ATIVO")

        botoes_memoria = QGridLayout()
        self.btn_m130 = QPushButton("Acionar M130 - Serial sem integracao")
        self.btn_m131 = QPushButton("Acionar M131 - Produto aprovado/finalizado")
        self.btn_m132 = QPushButton("Acionar M132 - Reteste apos 30 min")
        self.btn_m133 = QPushButton("Acionar M133 - 2 testes reprovados")
        self.btn_testar_clp = QPushButton("Testar conexao CLP")
        self.btn_reset_m130 = QPushButton("Reset M130")
        self.btn_reset_m131 = QPushButton("Reset M131")
        self.btn_reset_m132 = QPushButton("Reset M132")
        self.btn_reset_m133 = QPushButton("Reset M133")

        self.btn_m130.clicked.connect(lambda: self.acionar_memoria_clp("M130"))
        self.btn_m131.clicked.connect(lambda: self.acionar_memoria_clp("M131"))
        self.btn_m132.clicked.connect(lambda: self.acionar_memoria_clp("M132"))
        self.btn_m133.clicked.connect(lambda: self.acionar_memoria_clp("M133"))
        self.btn_testar_clp.clicked.connect(self.testar_conexao_clp)
        self.btn_reset_m130.clicked.connect(lambda: self.resetar_memoria_clp("M130"))
        self.btn_reset_m131.clicked.connect(lambda: self.resetar_memoria_clp("M131"))
        self.btn_reset_m132.clicked.connect(lambda: self.resetar_memoria_clp("M132"))
        self.btn_reset_m133.clicked.connect(lambda: self.resetar_memoria_clp("M133"))

        botoes_memoria.addWidget(self.btn_m130, 0, 0)
        botoes_memoria.addWidget(self.btn_m131, 0, 1)
        botoes_memoria.addWidget(self.btn_m132, 1, 0)
        botoes_memoria.addWidget(self.btn_m133, 1, 1)
        botoes_memoria.addWidget(self.btn_testar_clp, 2, 0, 1, 2)
        botoes_memoria.addWidget(self.btn_reset_m130, 3, 0)
        botoes_memoria.addWidget(self.btn_reset_m131, 3, 1)
        botoes_memoria.addWidget(self.btn_reset_m132, 4, 0)
        botoes_memoria.addWidget(self.btn_reset_m133, 4, 1)
        
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet("background-color: black; color: #00FF00; font-family: Courier;")
        
        layout.addWidget(self.label_clp)
        layout.addLayout(topo)
        layout.addWidget(self.label_status)
        layout.addWidget(self.status_memoria)
        layout.addLayout(botoes_memoria)
        layout.addWidget(self.terminal)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def atualizar_config_clp(self):
        self.controlador_clp.ip = self.input_ip_clp.text().strip() or self.controlador_clp.ip
        try:
            self.controlador_clp.porta = int(self.input_porta_clp.text().strip())
        except ValueError:
            self.log_terminal("Porta CLP invalida. Mantendo valor atual.")
        self.label_clp.setText(
            f"CLP {self.controlador_clp.ip}:{self.controlador_clp.porta} | Protocolo: {self.controlador_clp.protocolo}"
        )

    def testar_conexao_clp(self):
        self.atualizar_config_clp()
        try:
            conectado = self.controlador_clp.testar_conexao()
            self.status_memoria.setText(f"Conexao CLP: {'OK' if conectado else 'FALHA'}")
            self.log_terminal(f"[CLP] teste de conexao {'OK' if conectado else 'FALHA'}")
        except Exception as erro:
            self.status_memoria.setText("Conexao CLP: ERRO")
            self.log_terminal(f"[CLP] erro no teste de conexao: {erro}")

    def acionar_memoria_clp(self, memoria):
        self.atualizar_config_clp()
        try:
            self.status_memoria.setText(f"Acionando {memoria}...")
            resultado = self.controlador_clp.acionar_memoria(memoria)
            if resultado:
                self.ciclo_auto_memoria.registrar_ativacao(memoria)
                self.status_memoria.setText(f"{memoria} ligada e aguardando reset")
                self.log_terminal(f"[CLP] {memoria} ligada e aguardando reset manual")
            else:
                self.status_memoria.setText(f"{memoria} nao acionada")
                self.log_terminal(f"[CLP] {memoria} nao acionada")
        except Exception as erro:
            self.status_memoria.setText(f"Erro ao acionar {memoria}")
            self.log_terminal(f"[CLP] erro ao acionar {memoria}: {erro}")

    def resetar_memoria_clp(self, memoria):
        self.atualizar_config_clp()
        try:
            self.status_memoria.setText(f"Resetando {memoria}...")
            resultado = self.controlador_clp.resetar_memoria(memoria)
            if self.ciclo_auto_memoria.possui_pendente() and self.ciclo_auto_memoria.memoria_pendente == memoria:
                self.ciclo_auto_memoria.consumir_pendente()
            if resultado:
                self.status_memoria.setText(f"{memoria} resetada")
                self.log_terminal(f"[CLP] {memoria} resetada com sucesso")
            else:
                self.status_memoria.setText(f"{memoria} nao foi resetada")
                self.log_terminal(f"[CLP] {memoria} nao foi resetada")
        except Exception as erro:
            self.status_memoria.setText(f"Erro ao resetar {memoria}")
            self.log_terminal(f"[CLP] erro ao resetar {memoria}: {erro}")

    def validar_e_processar(self, serial_recebido):
        serial = serial_recebido.strip()
        if SegurancaSette.validar_serial(serial):
            self.log_terminal(f"Serial Lido: {serial}")

            # Sempre tenta resetar memoria pendente para liberar a maquina
            # antes de avaliar novo caso para a serial atual.
            self.ciclo_auto_memoria.processar_serial(
                serial,
                self.controlador_clp,
                self.log_terminal,
                memoria_alvo=None,
            )

            memoria_alvo, motivo = self.avaliador_casos.avaliar(serial)
            if memoria_alvo:
                self.log_terminal(f"[VALIDACAO] Serial {serial} caiu no caso {motivo} -> {memoria_alvo}")
                self.ciclo_auto_memoria.processar_serial(
                    serial,
                    self.controlador_clp,
                    self.log_terminal,
                    memoria_alvo=memoria_alvo,
                )
                self.log_terminal(f"[VALIDACAO] Fluxo bloqueado para serial {serial}")
                return

            if self.memoria_auto_alvo:
                self.ciclo_auto_memoria.processar_serial(
                    serial,
                    self.controlador_clp,
                    self.log_terminal,
                    memoria_alvo=self.memoria_auto_alvo,
                )
                self.memoria_auto_alvo = None

            self.fila_serial.enfileirar(serial)
            self.fila_eventos.put("processar")
        else:
            self.log_terminal(f"Leitura ignorada (fora do padrão 10): {serial}")

    def processar_fila_worker(self):
        while True:
            evento = self.fila_eventos.get()
            if evento != "processar":
                continue

            if self.processando:
                continue

            self.processando = True
            try:
                while not self.fila_serial.vazio():
                    serial = self.fila_serial.desenfileirar()
                    if not serial:
                        break
                    self.processar_envio(serial)
            finally:
                self.processando = False

    def processar_envio(self, serial):
        self.label_status.setText(" ENVIANDO DADOS...")
        self.log_terminal(f"[FLOW] Enviando serial {serial}")
        
        resposta, sucesso, v_est, v_uni, v_prog = self.api.enviar_estanqueidade(serial)

        self.log_terminal(
            f"[API] retorno={'OK' if sucesso else 'FALHA'} serial={serial} "
            f"value={v_est} unit={v_uni} program={v_prog}"
        )

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