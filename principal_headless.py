import json
import hmac
import hashlib
import os
import re
import time
from datetime import datetime, timezone

import psycopg2
import requests
import serial
from dotenv import load_dotenv

try:
    from evdev import InputDevice, ecodes
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

    def enviar_estanqueidade(self, serial_completo):
        endpoint = "/watertightness/log"
        auth = SegurancaSette.gerar_autenticacao("POST", "log")

        valor_mock = "30.5"
        unidade_mock = "Pa"
        prog_mock = "SETTE_V1"

        payload = {
            "serial": serial_completo[-10:],
            "name_jiga": os.getenv("NOME_JIGA"),
            "info": {
                "Value": valor_mock,
                "Status": "A",
                "Value_unit": unidade_mock,
                "Test_program": prog_mock,
                "Failure_cause": "",
            },
        }

        try:
            res = requests.post(
                f"{self.url_base}{endpoint}",
                json=payload,
                headers={"Authorization": auth},
                timeout=10,
            )
            return res.json(), res.status_code == 200, valor_mock, unidade_mock, prog_mock
        except Exception as erro_api:
            return {"erro": str(erro_api)}, False, valor_mock, unidade_mock, prog_mock


class LeitorSerial:
    def __init__(self):
        self.porta = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
        self.baudrate = int(os.getenv("SERIAL_BAUDRATE", "9600"))
        self.timeout = float(os.getenv("SERIAL_TIMEOUT", "1"))
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
            bruto = self.conexao.readline()
            if not bruto:
                return None

            texto = bruto.decode("utf-8", errors="ignore").strip()
            if not texto:
                return None

            match = re.search(r"\d{10}", texto)
            if match:
                return match.group(0)

            if SegurancaSette.validar_serial(texto):
                return texto

            print(f"[SERIAL] Leitura ignorada: {texto}")
            return None

        except Exception as e:
            print(f"[SERIAL] Erro de leitura: {e}")
            if self.conexao:
                try:
                    self.conexao.close()
                except Exception:
                    pass
            self.conexao = None
            return None


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


def processar_serial(serial_lido, api, persistencia):
    resposta, sucesso, v_est, v_uni, v_prog = api.enviar_estanqueidade(serial_lido)

    dados_log = {
        "serial": serial_lido,
        "tipo": "estanque",
        "jiga": os.getenv("NOME_JIGA"),
        "status": "A" if sucesso else "R",
        "valor_estanqueidade": v_est,
        "unidade_medida": v_uni,
        "programa_teste": v_prog,
    }

    persistencia.registrar_log(dados_log, resposta, sucesso)

    if sucesso:
        print(f"[OK] Serial {serial_lido} enviado com sucesso")
    else:
        print(f"[API] Falha no envio para serial {serial_lido}. Resposta: {resposta}")


def main():
    print("[INIT] Iniciando modo headless")
    print("[INIT] Aguardando leituras do leitor...")

    api = ClienteApiSpacecom()
    persistencia = GerenciadorPersistencia()
    leitor = criar_leitor()

    while True:
        serial_lido = leitor.ler_serial()
        if not serial_lido:
            continue

        processar_serial(serial_lido, api, persistencia)


if __name__ == "__main__":
    main()
