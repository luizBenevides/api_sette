import json
import hmac
import hashlib
import os
import re
import time
import threading
from queue import Queue, Empty
from datetime import datetime, timezone

import psycopg2
import requests
import serial
from dotenv import load_dotenv

try:
    from evdev import InputDevice, ecodes  # pyright: ignore[reportMissingImports]
except Exception:
    InputDevice = None
    ecodes = None

load_dotenv()


class SegurancaSette:
    @staticmethod
    def validar_serial(serial_lido):
        return bool(re.fullmatch(r"\d{10}", serial_lido))

    @staticmethod
    def gerar_autenticacao(metodo, nome_funcao):
        sistema = os.getenv("SPACECOM_SISTEMA", "sette")
        chave_secreta = os.getenv("SPACECOM_CHAVE_API", "")
        ruido = os.getenv("SPACECOM_RUIDO", "")

        agora_utc = datetime.now(timezone.utc)
        data_str = agora_utc.strftime("%m%H%d%M%Y")
        mensagem = f"{sistema.lower()}{data_str}{metodo.lower()}{ruido}{nome_funcao}"

        assinatura = hmac.new(
            chave_secreta.encode("utf-8"),
            mensagem.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return f"{sistema}:{assinatura}"


class ParserG3i:
    @staticmethod
    def limpar_linha(linha_bruta):
        return linha_bruta.strip().rstrip(";")

    @staticmethod
    def eh_pacote_ignorado(linha_bruta):
        linha = linha_bruta.strip().upper()
        return linha.startswith("XPO") or linha.startswith("XPA")

    @staticmethod
    def extrair_programa(linha_bruta):
        linha = ParserG3i.limpar_linha(linha_bruta)
        if not linha or ParserG3i.eh_pacote_ignorado(linha):
            return None

        partes = [parte.strip() for parte in linha.split(",")]
        if len(partes) >= 6 and partes[0].upper().startswith("ETT"):
            tipo_frame = partes[3].upper()
            if tipo_frame in ("N", "J"):
                programa = partes[5].strip().rstrip(";")
                return {
                    "tipo_frame": tipo_frame,
                    "programa_teste": programa or None,
                    "raw": linha_bruta.strip(),
                }

        return None

    @staticmethod
    def extrair_fim_teste(linha_bruta):
        linha = ParserG3i.limpar_linha(linha_bruta)
        if not linha or ParserG3i.eh_pacote_ignorado(linha):
            return None

        partes = [parte.strip() for parte in linha.split(",")]
        if len(partes) >= 4 and partes[0].upper().startswith("ETT") and partes[3].upper() == "XIR":
            return {
                "tipo_frame": "XIR",
                "serial_origem": partes[0],
                "raw": linha_bruta.strip(),
            }

        return None

    @staticmethod
    def extrair_resultado(linha_bruta):
        linha = ParserG3i.limpar_linha(linha_bruta)
        if not linha or ParserG3i.eh_pacote_ignorado(linha):
            return None

        partes = [parte.strip() for parte in linha.split(",")]
        if len(partes) < 8:
            return None

        if not partes[0].upper().startswith("ETT"):
            return None

        status = partes[3].upper()
        if status not in ("A", "R"):
            return None

        valor_pressao = partes[4]
        unidade_pressao = partes[5]
        valor_fuga = partes[6]
        unidade_fuga = partes[7].rstrip(";")

        valor_estanqueidade = valor_fuga or valor_pressao
        unidade_medida = unidade_fuga or unidade_pressao

        return {
            "tipo_frame": "resultado",
            "serial_origem": partes[0],
            "data_teste": partes[1],
            "hora_teste": partes[2],
            "status": status,
            "valor_pressao": valor_pressao,
            "unidade_pressao": unidade_pressao,
            "valor_estanqueidade": valor_estanqueidade,
            "unidade_medida": unidade_medida,
            "valor_fuga": valor_fuga,
            "unidade_fuga": unidade_fuga,
            "raw": linha_bruta.strip(),
        }


class GerenciadorPersistencia:
    def __init__(self):
        self.db_url = os.getenv("DB_URL")
        self.arquivo_txt = os.getenv("ARQUIVO_EMERGENCIA", "logs_emergencia.txt")

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
            cur.execute(
                query,
                (
                    dados_envio["serial"],
                    dados_envio["tipo"],
                    dados_envio["jiga"],
                    "A" if sucesso_api else "R",
                    json.dumps(resposta_api),
                    sucesso_api,
                    dados_envio["valor_estanqueidade"],
                    dados_envio["unidade_medida"],
                    dados_envio["programa_teste"],
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
            print("[DB] Gravado no Postgres")
        except Exception as erro_db:
            print(f"[DB] Erro: {erro_db}. Gravando fallback em arquivo...")
            self.salvar_em_txt(dados_envio, resposta_api, erro_db)

    def salvar_em_txt(self, dados, resposta, erro_db):
        with open(self.arquivo_txt, "a", encoding="utf-8") as f:
            f.write(
                f"DATA: {datetime.now()} | SERIAL: {dados['serial']} | "
                f"ERRO: {erro_db} | RESPOSTA: {resposta}\n"
            )


class ClienteApiSpacecom:
    def __init__(self):
        self.url_base = os.getenv("URL_BASE_SPACECOM", "")

    def enviar_estanqueidade(self, serial_completo, dados_teste=None):
        endpoint = "/watertightness/log"
        auth = SegurancaSette.gerar_autenticacao("POST", "log")

        dados_teste = dados_teste or {}
        valor_enviado = dados_teste.get("valor_estanqueidade")
        unidade_enviada = dados_teste.get("unidade_medida")
        programa_enviado = dados_teste.get("programa_teste")
        status_enviado = dados_teste.get("status")

        campos_obrigatorios = {
            "valor_estanqueidade": valor_enviado,
            "unidade_medida": unidade_enviada,
            "programa_teste": programa_enviado,
            "status": status_enviado,
        }
        faltando = [chave for chave, valor in campos_obrigatorios.items() if valor in (None, "")]
        if faltando:
            return {
                "erro": f"Dados insuficientes para envio: {', '.join(faltando)}"
            }, False, valor_enviado, unidade_enviada, programa_enviado

        payload = {
            "serial": serial_completo[-10:],
            "name_jiga": os.getenv("NOME_JIGA"),
            "info": {
                "Value": valor_enviado,
                "Status": status_enviado,
                "Value_unit": unidade_enviada,
                "Test_program": programa_enviado,
                "Failure_cause": "",
            },
        }

        print(
            "[API] Preparando envio: "
            f"serial={payload['serial']} status={status_enviado} "
            f"value={valor_enviado} unit={unidade_enviada} program={programa_enviado}"
        )
        print(f"[API] Payload: {json.dumps(payload, ensure_ascii=False)}")

        try:
            res = requests.post(
                f"{self.url_base}{endpoint}",
                json=payload,
                headers={"Authorization": auth},
                timeout=10,
            )
            try:
                corpo = res.json()
            except Exception:
                corpo = {"raw": res.text}

            print(f"[API] HTTP {res.status_code}")
            print(f"[API] Resposta: {json.dumps(corpo, ensure_ascii=False)}")
            return corpo, res.status_code == 200, valor_enviado, unidade_enviada, programa_enviado
        except Exception as erro_api:
            print(f"[API] Erro no envio: {erro_api}")
            return {"erro": str(erro_api)}, False, valor_enviado, unidade_enviada, programa_enviado


class LeitorSerial:
    def __init__(self):
        self.porta = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
        self.baudrate = int(os.getenv("SERIAL_BAUDRATE", "9600"))
        self.timeout = float(os.getenv("SERIAL_TIMEOUT", "1"))
        self.terminador = os.getenv("SERIAL_TERMINATOR", ";").encode("utf-8")
        self.conexao = None

    def conectar(self):
        while True:
            try:
                self.conexao = serial.Serial(
                    self.porta,
                    self.baudrate,
                    timeout=self.timeout,
                )
                print(f"[SERIAL] Conectado em {self.porta} @ {self.baudrate} bps")
                return
            except Exception as e:
                print(f"[SERIAL] Falha ao conectar na porta {self.porta}: {e}")
                time.sleep(3)

    def ler_serial(self):
        if self.conexao is None or not self.conexao.is_open:
            self.conectar()

        try:
            bruto = self.conexao.read_until(self.terminador)
            if not bruto:
                return None

            texto = bruto.decode("utf-8", errors="ignore").strip()
            if not texto:
                return None

            match = re.search(r"\d{10}", texto)
            if match:
                return match.group(0)

            return texto

        except Exception as e:
            print(f"[SERIAL] Erro de leitura: {e}")
            if self.conexao:
                try:
                    self.conexao.close()
                except Exception:
                    pass
            self.conexao = None
            return None

    def fechar(self):
        if self.conexao:
            try:
                self.conexao.close()
            except Exception:
                pass
            self.conexao = None


class LeitorHID:
    KEYMAP_DIGITOS = {
        "KEY_0": "0",
        "KEY_1": "1",
        "KEY_2": "2",
        "KEY_3": "3",
        "KEY_4": "4",
        "KEY_5": "5",
        "KEY_6": "6",
        "KEY_7": "7",
        "KEY_8": "8",
        "KEY_9": "9",
        "KEY_KP0": "0",
        "KEY_KP1": "1",
        "KEY_KP2": "2",
        "KEY_KP3": "3",
        "KEY_KP4": "4",
        "KEY_KP5": "5",
        "KEY_KP6": "6",
        "KEY_KP7": "7",
        "KEY_KP8": "8",
        "KEY_KP9": "9",
    }

    def __init__(self):
        self.device_path = os.getenv("HID_DEVICE", "/dev/input/event0")
        self.dispositivo = None
        self.buffer = ""

    def conectar(self):
        if InputDevice is None:
            raise RuntimeError("Biblioteca evdev nao instalada. Rode: pip install evdev")

        while True:
            try:
                self.dispositivo = InputDevice(self.device_path)
                print(f"[HID] Conectado em {self.device_path}: {self.dispositivo.name}")
                return
            except Exception as e:
                print(f"[HID] Falha ao abrir {self.device_path}: {e}")
                time.sleep(3)

    def _finalizar_buffer(self):
        texto = self.buffer.strip()
        self.buffer = ""

        if not texto:
            return None

        match = re.search(r"\d{10}", texto)
        if match:
            return match.group(0)

        return None

    def ler_serial(self):
        if self.dispositivo is None:
            self.conectar()

        try:
            for evento in self.dispositivo.read_loop():
                if evento.type != ecodes.EV_KEY:
                    continue

                # value 1 = key down
                if evento.value != 1:
                    continue

                codigo = ecodes.KEY[evento.code]

                if codigo in self.KEYMAP_DIGITOS:
                    self.buffer += self.KEYMAP_DIGITOS[codigo]
                    # Muitos leitores enviam exatamente 10 digitos sem Enter.
                    if len(self.buffer) == 10 and SegurancaSette.validar_serial(self.buffer):
                        retorno = self.buffer
                        self.buffer = ""
                        return retorno
                    continue

                if codigo in ("KEY_ENTER", "KEY_KPENTER"):
                    retorno = self._finalizar_buffer()
                    if retorno:
                        return retorno
                    continue

                # Qualquer outra tecla encerra o pacote atual.
                self._finalizar_buffer()

        except Exception as e:
            print(f"[HID] Erro de leitura: {e}")
            self.dispositivo = None
            time.sleep(1)
            return None

    def fechar(self):
        self.dispositivo = None


class LeitorHIDScanner:
    def __init__(self):
        self.leitor = LeitorHID()

    def ler_serial(self):
        return self.leitor.ler_serial()

    def fechar(self):
        self.leitor.fechar()


def criar_leitor():
    modo = os.getenv("INPUT_MODE", "auto").strip().lower()

    if modo == "hid":
        print("[INIT] INPUT_MODE=hid")
        return LeitorHID()

    if modo == "serial":
        print("[INIT] INPUT_MODE=serial")
        return LeitorSerial()

    # auto: tenta HID quando HID_DEVICE parece valido, senao usa serial.
    hid_device = os.getenv("HID_DEVICE", "").strip()
    if hid_device.startswith("/dev/input/"):
        print("[INIT] INPUT_MODE=auto -> usando HID")
        return LeitorHID()

    print("[INIT] INPUT_MODE=auto -> usando serial")
    return LeitorSerial()


def criar_leitores_duplos():
    leitor_maquina = LeitorSerial()
    leitor_scanner = None

    hid_device = os.getenv("HID_DEVICE", "").strip()
    if hid_device.startswith("/dev/input/"):
        leitor_scanner = LeitorHIDScanner()

    return leitor_maquina, leitor_scanner


def processar_serial(serial_lido, api, persistencia, dados_teste=None):
    print(f"[FLOW] Montando envio para serial do produto: {serial_lido}")
    resposta, sucesso, v_est, v_uni, v_prog = api.enviar_estanqueidade(serial_lido, dados_teste)

    dados_log = {
        "serial": serial_lido,
        "tipo": "estanque",
        "jiga": os.getenv("NOME_JIGA"),
        "status": (dados_teste or {}).get("status", "A" if sucesso else "R"),
        "valor_estanqueidade": v_est,
        "unidade_medida": v_uni,
        "programa_teste": v_prog,
    }

    persistencia.registrar_log(dados_log, resposta, sucesso)

    if sucesso:
        print(f"[OK] Serial {serial_lido} enviado com sucesso")
    else:
        print(f"[API] Falha no envio para serial {serial_lido}. Resposta: {resposta}")


def _dados_teste_do_resultado(resultado, programa_teste):
    return {
        "status": resultado["status"],
        "valor_estanqueidade": resultado["valor_estanqueidade"],
        "unidade_medida": resultado["unidade_medida"],
        "programa_teste": programa_teste or "SETTE_V1",
    }


def _tentar_envio(estado, api, persistencia):
    serial_produto = estado.get("serial_produto")
    resultado_pendente = estado.get("resultado_pendente")
    if not serial_produto or not resultado_pendente:
        print(
            "[FLOW] Aguardando pareamento: "
            f"serial={'ok' if serial_produto else 'faltando'} "
            f"resultado={'ok' if resultado_pendente else 'faltando'}"
        )
        return

    dados_teste = _dados_teste_do_resultado(
        resultado_pendente,
        estado.get("programa_teste"),
    )
    print(
        "[FLOW] Pareamento completo, enviando: "
        f"serial={serial_produto} status={dados_teste['status']} "
        f"value={dados_teste['valor_estanqueidade']} unit={dados_teste['unidade_medida']} "
        f"program={dados_teste['programa_teste']}"
    )
    processar_serial(serial_produto, api, persistencia, dados_teste)
    estado["serial_produto"] = None
    estado["resultado_pendente"] = None


def processar_linha(linha, api, persistencia, estado):
    linha_limpa = linha.strip()
    print(f"[RAW] {linha_limpa}")

    fim_teste = ParserG3i.extrair_fim_teste(linha_limpa)
    if fim_teste:
        print(f"[G3I] Fim de teste detectado: {fim_teste['raw']}")
        teste_em_andamento = estado.get("teste_em_andamento")
        if teste_em_andamento and teste_em_andamento.get("resultado"):
            estado["resultado_pendente"] = teste_em_andamento["resultado"]
            _tentar_envio(estado, api, persistencia)
        else:
            print("[G3I] XIR recebido sem resultado pronto ainda.")
        estado["teste_em_andamento"] = None
        return

    programa = ParserG3i.extrair_programa(linha_limpa)
    if programa:
        if programa.get("programa_teste"):
            estado["programa_teste"] = programa["programa_teste"]
            print(f"[G3I] Programa detectado ({programa['tipo_frame']}): {programa['programa_teste']}")

        if programa["tipo_frame"] == "J":
            estado["teste_em_andamento"] = {
                "programa_teste": estado.get("programa_teste"),
                "resultado": None,
            }
        return

    resultado = ParserG3i.extrair_resultado(linha_limpa)
    if resultado:
        if estado.get("teste_em_andamento") is None:
            estado["teste_em_andamento"] = {
                "programa_teste": estado.get("programa_teste"),
                "resultado": None,
            }

        estado["teste_em_andamento"]["resultado"] = resultado
        estado["serial_origem_g3i"] = resultado["serial_origem"]
        print(
            "[G3I] Resultado bruto recebido: "
            f"status={resultado['status']} valor={resultado['valor_estanqueidade']} "
            f"unidade={resultado['unidade_medida']} raw={resultado['raw']}"
        )

        print("[G3I] Aguardando frame XIR para fechar este ciclo de teste.")

        return

    if SegurancaSette.validar_serial(linha_limpa):
        estado["serial_produto"] = linha_limpa
        print(f"[BARCODE] Serial capturado: {linha_limpa}")
        _tentar_envio(estado, api, persistencia)
        return

    print(f"[RAW] Leitura ignorada: {linha_limpa}")


def _worker_leitor(nome_fonte, leitor, fila_eventos, parar_evento):
    while not parar_evento.is_set():
        try:
            valor = leitor.ler_serial()
            if valor:
                fila_eventos.put((nome_fonte, valor))
        except Exception as erro:
            fila_eventos.put(("erro", f"[{nome_fonte}] {erro}"))
            time.sleep(1)


def executar_fluxo_duplo(api, persistencia):
    estado = {
        "serial_produto": None,
        "programa_teste": os.getenv("PROGRAMA_TESTE_PADRAO", "SETTE_V1"),
        "resultado_pendente": None,
        "teste_em_andamento": None,
        "serial_origem_g3i": None,
    }

    leitor_maquina, leitor_scanner = criar_leitores_duplos()
    fila_eventos = Queue()
    parar_evento = threading.Event()
    threads = []

    threads.append(
        threading.Thread(
            target=_worker_leitor,
            args=("maquina", leitor_maquina, fila_eventos, parar_evento),
            daemon=True,
        )
    )

    if leitor_scanner is not None:
        threads.append(
            threading.Thread(
                target=_worker_leitor,
                args=("scanner", leitor_scanner, fila_eventos, parar_evento),
                daemon=True,
            )
        )

    for thread in threads:
        thread.start()

    try:
        while True:
            try:
                origem, valor = fila_eventos.get(timeout=0.5)
            except Empty:
                continue

            if origem == "erro":
                print(valor)
                continue

            if origem == "maquina":
                processar_linha(valor, api, persistencia, estado)
                continue

            if origem == "scanner":
                if SegurancaSette.validar_serial(valor):
                    estado["serial_produto"] = valor
                    print(f"[BARCODE] Serial capturado: {valor}")
                    _tentar_envio(estado, api, persistencia)
                    continue

                print(f"[BARCODE] Leitura ignorada: {valor}")
    except KeyboardInterrupt:
        print("[INIT] Encerrando...")
        parar_evento.set()
        try:
            leitor_maquina.fechar()
        except Exception:
            pass
        if leitor_scanner is not None:
            try:
                leitor_scanner.fechar()
            except Exception:
                pass


def main():
    print("[INIT] Iniciando modo headless")
    print("[INIT] Aguardando leituras do leitor...")

    api = ClienteApiSpacecom()
    persistencia = GerenciadorPersistencia()
    modo = os.getenv("INPUT_MODE", "auto").strip().lower()

    if modo == "dual":
        print("[INIT] INPUT_MODE=dual -> lendo maquina + scanner em paralelo")
        executar_fluxo_duplo(api, persistencia)
        return

    leitor = criar_leitor()
    estado = {
        "serial_produto": None,
        "programa_teste": os.getenv("PROGRAMA_TESTE_PADRAO", "SETTE_V1"),
        "resultado_pendente": None,
        "teste_em_andamento": None,
        "serial_origem_g3i": None,
    }

    while True:
        serial_lido = leitor.ler_serial()
        if not serial_lido:
            continue

        processar_linha(serial_lido, api, persistencia, estado)


if __name__ == "__main__":
    main()
