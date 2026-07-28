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
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QLockFile
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLabel,
    QListWidget,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QMessageBox,
)
from pynput import keyboard
import time

from core.memoria import obter_memoria_operacional
from core.clp import ControladorCLP
from core.auto_memoria import CicloAutomaticoMemorias
from core.validacoes_memoria import AvaliadorCasosMemoria
from core.database import conectar_banco, testar_banco

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
        self.arquivo_txt = os.getenv("ARQUIVO_EMERGENCIA", "emergencia.txt")
        self.dedup_seg = float(os.getenv("DB_DEDUP_SEG", "3"))

    def registrar_log(self, dados_envio, resposta_api, sucesso_api):
        try:
            conn = conectar_banco()
            cur = conn.cursor()
            status_log = dados_envio.get('status', 'A' if sucesso_api else 'R')
            tratamento_log = dados_envio.get('tratamento_clp') or ''
            chave_lock = f"{dados_envio['serial']}|{status_log}|{tratamento_log}"
            # Serializa processos concorrentes e impede duplicidade no banco.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (chave_lock,))
            cur.execute(
                """
                SELECT id FROM logs_producao
                WHERE serial = %s
                  AND resultado = %s
                  AND COALESCE(tratamento_clp, '') = %s
                  AND criado_em >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                ORDER BY criado_em DESC LIMIT 1
                """,
                (dados_envio['serial'], status_log, tratamento_log, self.dedup_seg),
            )
            duplicado = cur.fetchone()
            if duplicado:
                conn.rollback(); cur.close(); conn.close()
                print(f"[DB] Duplicata concorrente ignorada: serial={dados_envio['serial']} id_existente={duplicado[0]}")
                return False
            query = """
                INSERT INTO logs_producao 
                (serial, test_type, jiga_name, resultado, api_response_raw, enviado_api_externa, 
                 valor_estanqueidade, unidade_medida, programa_teste,
                 tratamento_clp, motivo_tratamento)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(query, (
                dados_envio['serial'], 
                dados_envio['tipo'], 
                dados_envio['jiga'], 
                status_log,
                json.dumps(resposta_api),
                sucesso_api,
                dados_envio['valor_estanqueidade'],
                dados_envio['unidade_medida'],
                dados_envio.get('programa_teste'),
                dados_envio.get('tratamento_clp'),
                dados_envio.get('motivo_tratamento')
            ))
            conn.commit()
            cur.close()
            conn.close()
            print(" GRAVADO NO POSTGRES")
            return True
        except Exception as e:
            print(f" ERRO BANCO: {e}")
            self.salvar_em_txt(dados_envio, resposta_api, e)

    def registrar_tratamento(self, serial, memoria, motivo):
        serial = serial or 'LEITURA_VAZIA'
        motivo_completo = f'{memoria}:{motivo}'
        resposta = {'bloqueado': True, 'memoria': 'M2100', 'validacao': memoria, 'motivo': motivo}
        conn = None
        try:
            conn = conectar_banco()
            with conn.cursor() as cur:
                chave_lock = f"TRATAMENTO|{serial}|{motivo_completo}"
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (chave_lock,))
                cur.execute(
                    """
                    SELECT id FROM logs_producao
                    WHERE serial = %s AND tratamento_clp = 'M2100'
                      AND motivo_tratamento = %s
                    ORDER BY id ASC LIMIT 1 FOR UPDATE
                    """,
                    (serial, motivo_completo),
                )
                existente = cur.fetchone()
                if existente:
                    cur.execute(
                        """
                        UPDATE logs_producao
                           SET criado_em = CURRENT_TIMESTAMP,
                               resultado = 'R', enviado_api_externa = FALSE,
                               api_response_raw = %s, jiga_name = %s
                         WHERE id = %s
                        """,
                        (json.dumps(resposta), os.getenv('NOME_JIGA'), existente[0]),
                    )
                    conn.commit()
                    print(f"[DB] Tratamento existente atualizado: id={existente[0]} serial={serial} erro={memoria}")
                    return existente[0]

                cur.execute(
                    """
                    INSERT INTO logs_producao
                    (serial, test_type, jiga_name, resultado, api_response_raw,
                     enviado_api_externa, tratamento_clp, motivo_tratamento)
                    VALUES (%s, 'estanque', %s, 'R', %s, FALSE, 'M2100', %s)
                    RETURNING id
                    """,
                    (serial, os.getenv('NOME_JIGA'), json.dumps(resposta), motivo_completo),
                )
                novo_id = cur.fetchone()[0]
                conn.commit()
                print(f"[DB] Primeiro registro do tratamento: id={novo_id} serial={serial} erro={memoria}")
                return novo_id
        except Exception as erro:
            if conn is not None:
                conn.rollback()
            dados = {'serial': serial, 'tratamento_clp': 'M2100', 'motivo_tratamento': motivo_completo}
            print(f"[DB] Erro ao gravar/atualizar tratamento: {erro}")
            self.salvar_em_txt(dados, resposta, erro)
            return None
        finally:
            if conn is not None:
                conn.close()

    def carregar_ultimos_estados(self):
        """Retorna somente o evento mais recente de cada serial."""
        conn = conectar_banco()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (serial)
                           criado_em, serial, resultado, tratamento_clp, motivo_tratamento
                    FROM logs_producao
                    WHERE serial IS NOT NULL AND TRIM(serial) <> ''
                    ORDER BY serial, criado_em DESC, id DESC
                """)
                return cur.fetchall()
        finally:
            conn.close()

    def salvar_em_txt(self, dados, resposta, erro_db):
        with open(self.arquivo_txt, "a", encoding="utf-8") as f:
            f.write(f"DATA: {datetime.now()} | SERIAL: {dados['serial']} | ERRO: {erro_db} | RESPOSTA: {resposta}\n")


class FilaSerialFIFO:
    def __init__(self):
        self.itens = deque()
        self.lock = threading.Lock()

    def enfileirar(self, serial):
        with self.lock:
            # Evita duplicados na fila de espera
            if serial in self.itens:
                print(f"[QUEUE] Serial {serial} já está na fila. Ignorando duplicata.")
                return
                
            self.itens.append(serial)
            print(f"[QUEUE] Serial enfileirado: {serial} | total={len(self.itens)}")

    def desenfileirar(self):
        with self.lock:
            if not self.itens:
                return None
            serial = self.itens.popleft()
            print(f"[QUEUE] Serial liberado para envio: {serial} | restante={len(self.itens)}")
            return serial

    def vazio(self):
        with self.lock:
            return not self.itens

class ClienteApiSpacecom:
    def __init__(self):
        self.url_base = os.getenv("URL_BASE_SPACECOM")

    def enviar_estanqueidade(self, serial_completo, dados_teste=None):
        """Envia dados de estanqueidade para a API externa."""
        endpoint = "/watertightness/log"
        auth = SegurancaSette.gerar_autenticacao("POST", "log")

        dados_teste = dados_teste or {}
        valor_envio = dados_teste.get("valor_estanqueidade") or os.getenv("VALOR_ESTANQUEIDADE_PADRAO") or "0"
        unidade_envio = dados_teste.get("unidade_medida") or os.getenv("UNIDADE_ESTANQUEIDADE_PADRAO") or "ml/min"
        programa_envio = dados_teste.get("programa_teste") or os.getenv("PROGRAMA_TESTE_PADRAO") or "SETTE_V1"
        status_envio = "A"

        if not all([valor_envio, unidade_envio, programa_envio, status_envio]):
            return {
                "erro": "Dados de envio incompletos no .env. Configure VALOR_ESTANQUEIDADE_PADRAO, UNIDADE_ESTANQUEIDADE_PADRAO e PROGRAMA_TESTE_PADRAO."
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
class JanelaEscritaManual(QDialog):
    """Tela de bancada para escrever pontos Modbus e conferir a IHM do CLP."""
    def __init__(self, controlador, logger, parent=None):
        super().__init__(parent)
        self.controlador, self.logger = controlador, logger
        self.setWindowTitle("Teste manual de escrita no CLP")
        self.resize(520, 390)
        self.tipo = QComboBox(); self.tipo.addItems(["Holding register", "Coil"])
        self.endereco = QSpinBox(); self.endereco.setRange(0, 65535)
        self.endereco.setValue(int(os.getenv("CLP_REGISTRADOR_IHM", "0")))
        self.valor = QSpinBox(); self.valor.setRange(0, 65535)
        self.device_id = QSpinBox(); self.device_id.setRange(0, 247)
        self.device_id.setValue(int(os.getenv("CLP_DEVICE_ID", "1")))
        self.historico = QListWidget()
        botao = QPushButton("Escrever agora"); botao.clicked.connect(self.escrever)
        form = QFormLayout(); form.addRow("Area Modbus:", self.tipo)
        form.addRow("Endereco (base zero):", self.endereco); form.addRow("Valor:", self.valor)
        form.addRow("Device/Unit ID:", self.device_id)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(botao)
        layout.addWidget(QLabel("Historico desta sessao:")); layout.addWidget(self.historico)
        fechar = QDialogButtonBox(QDialogButtonBox.Close); fechar.rejected.connect(self.close); layout.addWidget(fechar)

    def escrever(self):
        endereco, valor, device_id = self.endereco.value(), self.valor.value(), self.device_id.value()
        try:
            if self.tipo.currentText() == "Coil":
                self.controlador.escrever_coil(endereco, valor != 0, device_id)
                descricao = f"Coil {endereco} = {1 if valor else 0} (device {device_id})"
            else:
                self.controlador.escrever_registrador(endereco, valor, device_id)
                descricao = f"Holding {endereco} = {valor} (device {device_id})"
            self.historico.insertItem(0, f"OK - {descricao}"); self.logger(f"[CLP MANUAL] {descricao}")
        except Exception as erro:
            self.historico.insertItem(0, f"ERRO - {erro}"); self.logger(f"[CLP MANUAL] erro: {erro}")


TRATAMENTOS_POPUP = {
    "M130": ("SERIAL SEM INTEGRACAO", "Leia uma serial valida para liberar a esteira."),
    "M131": ("PRODUTO JA APROVADO", "Produto aprovado ou finalizado. Leia a proxima serial valida."),
    "M132": ("RETESTE BLOQUEADO", "Aguarde o intervalo minimo de 30 minutos."),
    "M133": ("LIMITE DE RETESTES", "Produto reprovado duas vezes. Retire a peca da linha."),
}


class PopupTratamento(QDialog):
    def __init__(self, memoria, parent=None):
        super().__init__(parent)
        titulo, mensagem = TRATAMENTOS_POPUP[memoria]
        self.setWindowTitle(titulo); self.setModal(False)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False); self.setMinimumSize(760, 420)
        self.setStyleSheet("QDialog { background:#9b111e; } QLabel { color:white; }")
        cabecalho = QLabel(titulo); cabecalho.setAlignment(Qt.AlignCenter)
        cabecalho.setStyleSheet("font-size:38px; font-weight:bold;")
        texto = QLabel(mensagem + "\n\nESTEIRA PARADA")
        texto.setWordWrap(True); texto.setAlignment(Qt.AlignCenter)
        texto.setStyleSheet("font-size:25px; font-weight:bold;")
        layout = QVBoxLayout(self); layout.addStretch(); layout.addWidget(cabecalho)
        layout.addWidget(texto); layout.addStretch()


class DashboardSeriais(QDialog):
    """Dashboard industrial no mesmo padrao visual dos demais softwares SETTE."""
    CORES_LED = {"vermelho": "#ef4444", "verde": "#4ade80", "amarelo": "#facc15"}

    def __init__(self, conectar_clp, parent=None):
        super().__init__(parent)
        self.conectar_clp = conectar_clp
        self.total = 0
        self.aprovadas = 0
        self.tratamentos = 0
        self.registros_por_serial = {}
        self.setWindowTitle("SETTE | Dashboard de Coleta")
        self.resize(1440, 850)
        self.setMinimumSize(1080, 680)
        self.setStyleSheet("""
            QDialog { background:#08090f; color:#f8fafc; }
            QLabel { color:#f8fafc; }
            QPushButton { background:#242633; color:#d1d5db; border:1px solid #343746;
                          border-radius:5px; padding:10px 18px; font-size:14px; font-weight:600; }
            QPushButton:hover { background:#303341; border-color:#60a5fa; color:white; }
            QPushButton:pressed { background:#1d4ed8; }
            QTableWidget { background:#20222d; alternate-background-color:#242733; color:#e5e7eb;
                           border:0; gridline-color:#30333f; selection-background-color:#334155;
                           font-size:14px; }
            QHeaderView::section { background:#1b1d27; color:#9ca3af; border:0;
                                   border-bottom:1px solid #343746; padding:12px; font-weight:700; }
            QTableCornerButton::section { background:#1b1d27; border:0; }
            QScrollBar:vertical { background:#171923; width:12px; }
            QScrollBar::handle:vertical { background:#3b3f50; border-radius:6px; min-height:28px; }
        """)

        marca = QLabel("SETTE  /  COLETA DE SERIAIS")
        marca.setStyleSheet("font-size:13px; color:#64748b; font-weight:700; letter-spacing:1px;")
        titulo = QLabel("Dashboard de Produção")
        titulo.setStyleSheet("font-size:30px; font-weight:700; color:#f8fafc;")
        bloco_titulo = QVBoxLayout(); bloco_titulo.setSpacing(3)
        bloco_titulo.addWidget(marca); bloco_titulo.addWidget(titulo)

        self.led = QLabel(); self.led.setFixedSize(22, 22)
        self.texto_clp = QLabel("CLP desconectado")
        self.texto_clp.setStyleSheet("font-size:15px; font-weight:700;")
        self.btn_conectar = QPushButton("Conectar com o CLP")
        self.btn_conectar.clicked.connect(self._conectar)
        topo = QHBoxLayout(); topo.setContentsMargins(8, 4, 8, 8)
        topo.addLayout(bloco_titulo); topo.addStretch()
        topo.addWidget(self.led); topo.addWidget(self.texto_clp); topo.addSpacing(10); topo.addWidget(self.btn_conectar)

        card_aprovado, self.lbl_aprovadas, self.lbl_pct_aprovadas = self._criar_card(
            "APROVADAS", "#172920", "#4ade80", "peças aprovadas")
        card_tratamento, self.lbl_tratamentos, self.lbl_pct_tratamentos = self._criar_card(
            "EM TRATAMENTO", "#302021", "#f87171", "esteira interrompida")
        card_total, self.lbl_total, self.lbl_pct_total = self._criar_card(
            "TOTAL", "#1d2533", "#60a5fa", "seriais coletadas")
        cards = QHBoxLayout(); cards.setSpacing(14)
        cards.addWidget(card_aprovado); cards.addWidget(card_tratamento); cards.addWidget(card_total)

        titulo_tabela = QLabel("SERIAIS GRAVADAS")
        titulo_tabela.setStyleSheet("font-size:14px; color:#94a3b8; font-weight:700; padding:4px;")
        self.tabela = QTableWidget(0, 5)
        self.tabela.setHorizontalHeaderLabels(["DATA / HORA", "SERIAL", "ENVIO INICIAL", "VALIDAÇÃO", "MOTIVO"])
        self.tabela.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabela.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.setShowGrid(False)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.verticalHeader().setDefaultSectionSize(42)
        cabecalho = self.tabela.horizontalHeader()
        for coluna in range(4):
            cabecalho.setSectionResizeMode(coluna, QHeaderView.ResizeToContents)
        cabecalho.setSectionResizeMode(4, QHeaderView.Stretch)

        painel_tabela = QFrame(); painel_tabela.setObjectName("painelTabela")
        painel_tabela.setStyleSheet("QFrame#painelTabela { background:#1f212b; border-radius:5px; }")
        layout_tabela = QVBoxLayout(painel_tabela); layout_tabela.setContentsMargins(18, 16, 18, 18)
        layout_tabela.addWidget(titulo_tabela); layout_tabela.addWidget(self.tabela)

        layout = QVBoxLayout(self); layout.setContentsMargins(24, 20, 24, 22); layout.setSpacing(14)
        layout.addLayout(topo); layout.addLayout(cards, 4); layout.addWidget(painel_tabela, 3)
        self.definir_estado("vermelho", "CLP desconectado")

    def _criar_card(self, titulo, fundo, destaque, legenda):
        card = QFrame(); card.setMinimumHeight(230)
        card.setStyleSheet(f"QFrame {{ background:{fundo}; border-radius:4px; }}")
        lbl_titulo = QLabel(titulo); lbl_titulo.setAlignment(Qt.AlignCenter)
        lbl_titulo.setStyleSheet("font-size:22px; font-weight:600; color:#f8fafc;")
        numero = QLabel("0"); numero.setAlignment(Qt.AlignCenter)
        numero.setStyleSheet(f"font-size:78px; font-weight:700; color:{destaque};")
        lbl_legenda = QLabel(legenda); lbl_legenda.setAlignment(Qt.AlignCenter)
        lbl_legenda.setStyleSheet("font-size:15px; color:#7c8393;")
        percentual = QLabel("0%"); percentual.setAlignment(Qt.AlignCenter)
        percentual.setStyleSheet("font-size:17px; font-weight:700; color:#f8fafc;")
        box = QVBoxLayout(card); box.setContentsMargins(18, 24, 18, 22)
        box.addWidget(lbl_titulo); box.addStretch(); box.addWidget(numero)
        box.addWidget(lbl_legenda); box.addWidget(percentual); box.addStretch()
        return card, numero, percentual

    def definir_estado(self, cor, texto):
        cor_css = self.CORES_LED[cor]
        self.led.setStyleSheet(f"background:{cor_css}; border-radius:11px; border:2px solid #475569;")
        self.texto_clp.setText(texto)

    def _conectar(self):
        self.btn_conectar.setEnabled(False); self.btn_conectar.setText("Conectando...")
        try:
            conectado = bool(self.conectar_clp())
            self.definir_estado("verde" if conectado else "vermelho", "CLP conectado" if conectado else "CLP desconectado")
        finally:
            self.btn_conectar.setText("Conectar com o CLP"); self.btn_conectar.setEnabled(True)

    def _atualizar_indicadores(self):
        self.total = len(self.registros_por_serial)
        self.aprovadas = sum(1 for registro in self.registros_por_serial.values() if registro["validacao"] == "-")
        self.tratamentos = self.total - self.aprovadas
        self.lbl_aprovadas.setText(str(self.aprovadas))
        self.lbl_tratamentos.setText(str(self.tratamentos))
        self.lbl_total.setText(str(self.total))
        pct_aprovadas = (self.aprovadas / self.total * 100) if self.total else 0
        pct_tratamentos = (self.tratamentos / self.total * 100) if self.total else 0
        self.lbl_pct_aprovadas.setText(f"{pct_aprovadas:.0f}%")
        self.lbl_pct_tratamentos.setText(f"{pct_tratamentos:.0f}%")
        self.lbl_pct_total.setText("100%" if self.total else "0%")

    def registrar_leitura(self, serial, status, validacao="-", motivo="-", horario=None):
        chave = (serial or "(vazia)").strip()
        self.registros_por_serial[chave] = {
            "horario": horario or datetime.now(), "serial": chave, "status": status,
            "validacao": validacao or "-", "motivo": motivo or "-",
        }
        self._renderizar_registros()

    def carregar_do_banco(self, registros):
        self.registros_por_serial.clear()
        for criado_em, serial, resultado, tratamento_clp, motivo_tratamento in registros:
            motivo = motivo_tratamento or "-"
            validacao = "-"
            if tratamento_clp:
                validacao = motivo.split(":", 1)[0] if ":" in motivo else tratamento_clp
                motivo = motivo.split(":", 1)[1] if ":" in motivo else motivo
            self.registros_por_serial[str(serial).strip()] = {
                "horario": criado_em, "serial": str(serial).strip(),
                "status": "BLOQUEADA" if tratamento_clp else "APROVADA (A)",
                "validacao": validacao, "motivo": motivo,
            }
        self._renderizar_registros()

    def _renderizar_registros(self):
        self._atualizar_indicadores()
        registros = sorted(
            self.registros_por_serial.values(),
            key=lambda item: item["horario"].timestamp() if hasattr(item["horario"], "timestamp") else 0,
            reverse=True,
        )
        self.tabela.setRowCount(0)
        for registro in registros[:1000]:
            linha = self.tabela.rowCount(); self.tabela.insertRow(linha)
            horario = registro["horario"]
            if hasattr(horario, "strftime"):
                horario = horario.strftime("%d/%m/%Y %H:%M:%S")
            valores = [horario, registro["serial"], registro["status"], registro["validacao"], registro["motivo"]]
            em_tratamento = registro["validacao"] != "-"
            fundo = QColor("#352f22" if em_tratamento else "#1d2b25")
            texto = QColor("#fde68a" if em_tratamento else "#d1fae5")
            for coluna, valor in enumerate(valores):
                item = QTableWidgetItem(str(valor)); item.setBackground(fundo); item.setForeground(texto)
                self.tabela.setItem(linha, coluna, item)


class InterfaceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SETTE - Integrador (Global)")
        self.resize(900, 650)
        self.memoria_operacional = obter_memoria_operacional()
        self.controlador_clp = ControladorCLP()
        self.ciclo_auto_memoria = CicloAutomaticoMemorias()
        self.avaliador_casos = AvaliadorCasosMemoria()
        self.memoria_auto_alvo = None
        self.popup_tratamento = None
        self.dashboard = None
        self.ultima_serial_capturada = None
        self.ultima_serial_capturada_em = 0.0
        self.debounce_serial_seg = float(os.getenv("SERIAL_DEBOUNCE_SEG", "2"))
        
        self.api = ClienteApiSpacecom()
        self.dados = GerenciadorPersistencia()
        self.fila_serial = FilaSerialFIFO()
        self.processando = False
        self.fila_eventos = Queue()
        self.thread_worker = threading.Thread(target=self.processar_fila_worker, daemon=True)
        self.thread_worker.start()
        
        self.configurar_ui()
        self.dashboard = DashboardSeriais(self.testar_conexao_clp, self)
        self.carregar_dashboard_do_banco()

        # Inicializa o ouvinte global somente depois de todas as telas estarem prontas.
        self.ouvinte = OuvinteGlobal()
        self.ouvinte.serial_capturado.connect(self.validar_e_processar)

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

        self.status_memoria = QLabel("Intertravamento da esteira: somente M2100")
        self.status_memoria.setStyleSheet("color: #444;")
        if self.ciclo_auto_memoria.habilitado():
            self.status_memoria.setText("Modo automatico de memorias ATIVO")

        botoes_memoria = QGridLayout()
        self.btn_m130 = QPushButton("Simular: Serial sem integracao")
        self.btn_m131 = QPushButton("Simular: Produto aprovado/finalizado")
        self.btn_m132 = QPushButton("Simular: Reteste antes de 30 min")
        self.btn_m133 = QPushButton("Simular: 2 testes reprovados")
        self.btn_testar_clp = QPushButton("Testar conexao CLP")
        self.btn_testar_banco = QPushButton("Testar conexao banco")
        self.btn_escrita_manual = QPushButton("Abrir escrita manual de registradores")
        self.btn_dashboard = QPushButton("Abrir dashboard de seriais")
        self.btn_dashboard.setStyleSheet("font-size:16px; font-weight:bold; min-height:38px;")
        self.btn_liberar_m2100 = QPushButton("Teste manual: liberar esteira (M2100=False)")

        self.btn_m130.clicked.connect(lambda: self.parar_esteira_e_exibir("M130"))
        self.btn_m131.clicked.connect(lambda: self.parar_esteira_e_exibir("M131"))
        self.btn_m132.clicked.connect(lambda: self.parar_esteira_e_exibir("M132"))
        self.btn_m133.clicked.connect(lambda: self.parar_esteira_e_exibir("M133"))
        self.btn_testar_clp.clicked.connect(self.testar_conexao_clp)
        self.btn_testar_banco.clicked.connect(self.testar_conexao_banco)
        self.btn_escrita_manual.clicked.connect(self.abrir_escrita_manual)
        self.btn_dashboard.clicked.connect(self.abrir_dashboard)
        self.btn_liberar_m2100.clicked.connect(self.liberar_esteira_teste_manual)

        botoes_memoria.addWidget(self.btn_m130, 0, 0)
        botoes_memoria.addWidget(self.btn_m131, 0, 1)
        botoes_memoria.addWidget(self.btn_m132, 1, 0)
        botoes_memoria.addWidget(self.btn_m133, 1, 1)
        botoes_memoria.addWidget(self.btn_testar_clp, 2, 0, 1, 2)
        botoes_memoria.addWidget(self.btn_testar_banco, 3, 0, 1, 2)
        botoes_memoria.addWidget(self.btn_escrita_manual, 4, 0, 1, 2)
        botoes_memoria.addWidget(self.btn_liberar_m2100, 5, 0, 1, 2)
        
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet("background-color: black; color: #00FF00; font-family: Courier;")
        
        layout.addWidget(self.label_clp)
        layout.addLayout(topo)
        layout.addWidget(self.btn_dashboard)
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
            return conectado
        except Exception as erro:
            self.status_memoria.setText("Conexao CLP: ERRO")
            self.log_terminal(f"[CLP] erro no teste de conexao: {erro}")
            return False

    def abrir_dashboard(self):
        self.dashboard.showMaximized(); self.dashboard.raise_(); self.dashboard.activateWindow()

    def carregar_dashboard_do_banco(self):
        try:
            registros = self.dados.carregar_ultimos_estados()
            self.dashboard.carregar_do_banco(registros)
            self.log_terminal(f"[DB] Dashboard carregado: {len(registros)} seriais unicas")
        except Exception as erro:
            self.log_terminal(f"[DB] Nao foi possivel carregar o dashboard: {erro}")

    def testar_conexao_banco(self):
        try:
            conectado = testar_banco()
            self.label_status.setText("BANCO DE DADOS CONECTADO" if conectado else "FALHA NO BANCO")
            self.log_terminal(f"[DB] teste de conexao {'OK' if conectado else 'FALHA'}")
        except Exception as erro:
            self.label_status.setText("ERRO NA CONEXAO COM BANCO")
            self.log_terminal(f"[DB] erro no teste: {erro}")

    def abrir_escrita_manual(self):
        self.atualizar_config_clp()
        JanelaEscritaManual(self.controlador_clp, self.log_terminal, self).exec()

    def parar_esteira_e_exibir(self, memoria):
        self.atualizar_config_clp()
        m2100_acionada = False
        try:
            self.controlador_clp.parar_esteira()
            m2100_acionada = True
            self.status_memoria.setText(f"ESTEIRA PARADA: M2100=TRUE ({memoria})")
            self.log_terminal(f"[SEGURANCA] M2100 ligada pelo tratamento {memoria}")
        except Exception as erro:
            self.log_terminal(f"[SEGURANCA] ERRO CRITICO ao ligar M2100: {erro}")
        if self.popup_tratamento is not None:
            self.popup_tratamento.close()
        self.popup_tratamento = PopupTratamento(memoria, self)
        self.popup_tratamento.show(); self.popup_tratamento.raise_(); self.popup_tratamento.activateWindow()
        if self.dashboard is not None:
            if m2100_acionada:
                self.dashboard.definir_estado("amarelo", f"TRATAMENTO ATIVO - {memoria} - M2100=True")
            else:
                self.dashboard.definir_estado("vermelho", "CLP desconectado - falha ao acionar M2100")

    def liberar_esteira_por_serial_valida(self, serial):
        self.atualizar_config_clp()
        try:
            self.controlador_clp.liberar_esteira()
            self.status_memoria.setText("ESTEIRA LIBERADA: M2100=FALSE")
            self.log_terminal(f"[SEGURANCA] Serial valida {serial}: M2100 desligada")
            if self.popup_tratamento is not None:
                self.popup_tratamento.close(); self.popup_tratamento = None
            if self.dashboard is not None:
                self.dashboard.definir_estado("verde", "CLP conectado - esteira liberada")
            return True
        except Exception as erro:
            self.log_terminal(f"[SEGURANCA] Nao foi possivel liberar M2100: {erro}")
            return False

    def liberar_esteira_teste_manual(self):
        self.atualizar_config_clp()
        try:
            self.controlador_clp.liberar_esteira()
            self.status_memoria.setText("TESTE MANUAL: M2100=FALSE")
            self.log_terminal("[CLP TESTE] M2100 desligada manualmente")
            if self.popup_tratamento is not None:
                self.popup_tratamento.close(); self.popup_tratamento = None
            if self.dashboard is not None:
                self.dashboard.definir_estado("verde", "CLP conectado - liberacao manual")
        except Exception as erro:
            self.log_terminal(f"[CLP TESTE] erro ao desligar M2100: {erro}")

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
        agora = time.monotonic()
        if serial and serial == self.ultima_serial_capturada and (agora - self.ultima_serial_capturada_em) < self.debounce_serial_seg:
            self.log_terminal(f"[LEITOR] Duplicata imediata ignorada: {serial}")
            return
        self.ultima_serial_capturada = serial
        self.ultima_serial_capturada_em = agora
        if SegurancaSette.validar_serial(serial):
            self.log_terminal(f"Serial Lido: {serial}")

            # 1) Avalia se a serial cai em algum caso de bloqueio (M131, M132, M133)
            memoria_bloqueio, motivo = self.avaliador_casos.avaliar(serial)
            if memoria_bloqueio:
                self.log_terminal(f"[VALIDACAO] Serial {serial} caiu no caso {motivo} -> {memoria_bloqueio}")

            # 2) Processa o CLP apenas uma vez (seja para resetar o anterior ou para aplicar novo bloqueio)
            if memoria_bloqueio:
                self.dados.registrar_tratamento(serial, memoria_bloqueio, motivo)
                self.dashboard.registrar_leitura(serial, "BLOQUEADA", memoria_bloqueio, motivo)
                self.parar_esteira_e_exibir(memoria_bloqueio)
                self.log_terminal(f"[VALIDACAO] Fluxo bloqueado por {memoria_bloqueio}; CLP M2100=TRUE")
                return
            if not self.liberar_esteira_por_serial_valida(serial):
                self.dashboard.registrar_leitura(serial, "NAO ENVIADA", "FALHA CLP", "Nao foi possivel liberar M2100")
                return

            self.dashboard.registrar_leitura(serial, "APROVADA (A)")

            self.fila_serial.enfileirar(serial)
            self.fila_eventos.put("processar")
        else:
            self.log_terminal(f"Leitura ignorada (fora do padrão 10): {serial}")
            self.dados.registrar_tratamento(serial, "M130", "serial_sem_integracao")
            self.dashboard.registrar_leitura(serial, "BLOQUEADA", "M130", "serial_sem_integracao")
            self.parar_esteira_e_exibir("M130")

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
            'status': 'A',
            'valor_estanqueidade': v_est,
            'unidade_medida': v_uni,
            'programa_teste': v_prog
            ,'tratamento_clp': None
            ,'motivo_tratamento': None
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
    caminho_lock = os.path.join(os.getenv("TEMP", os.getcwd()), "sette_integrador_instancia.lock")
    lock_instancia = QLockFile(caminho_lock)
    lock_instancia.setStaleLockTime(10000)
    if not lock_instancia.tryLock(0):
        QMessageBox.warning(None, "SETTE ja esta aberto", "O integrador SETTE ja esta em execucao neste computador.")
        sys.exit(2)
    window = InterfaceApp()
    if "--dashboard" in sys.argv:
        window.dashboard.setWindowFlag(Qt.Window, True)
        window.dashboard.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())
