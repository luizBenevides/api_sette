"""Controlador de CLP para acionar memorias por Ethernet.

Assume Modbus TCP como transporte padrao enquanto o protocolo final nao for confirmado.
"""

import os


MEMORIAS_PADRAO = {
    "M130": 130,
    "M131": 131,
    "M132": 132,
    "M133": 133,
}


class ControladorCLP:
    def __init__(self):
        self.ip = os.getenv("CLP_IP", "192.168.1.5")
        self.porta = int(os.getenv("CLP_PORTA_TCP", "502"))
        self.protocolo = os.getenv("CLP_PROTOCOLO", "modbus_tcp").strip().lower()
        self.timeout_seg = float(os.getenv("CLP_TIMEOUT_SEG", "3"))

    def _obter_cliente_modbus(self):
        try:
            from pymodbus.client import ModbusTcpClient
        except Exception as erro:
            raise RuntimeError(
                "Dependencia pymodbus nao instalada. Rode: pip install pymodbus"
            ) from erro

        return ModbusTcpClient(host=self.ip, port=self.porta, timeout=self.timeout_seg)

    def testar_conexao(self):
        print(f"[CLP] Testando conexao em {self.ip}:{self.porta} via {self.protocolo}")
        cliente = self._obter_cliente_modbus()
        try:
            conectado = bool(cliente.connect())
            print(f"[CLP] Conexao {'OK' if conectado else 'FALHA'} em {self.ip}:{self.porta}")
            return conectado
        finally:
            try:
                cliente.close()
            except Exception:
                pass

    def escrever_memoria(self, nome_memoria, estado):
        if self.protocolo != "modbus_tcp":
            raise RuntimeError(f"Protocolo CLP nao suportado: {self.protocolo}")

        if nome_memoria not in MEMORIAS_PADRAO:
            raise ValueError(f"Memoria desconhecida: {nome_memoria}")

        print(
            f"[CLP] Escrevendo {nome_memoria}={1 if estado else 0} "
            f"(coil {MEMORIAS_PADRAO[nome_memoria]}) em {self.ip}:{self.porta}"
        )
        cliente = self._obter_cliente_modbus()
        if not cliente.connect():
            print(f"[CLP] Falha ao conectar antes da escrita de {nome_memoria}")
            raise ConnectionError(f"Nao foi possivel conectar no CLP {self.ip}:{self.porta}")

        try:
            resultado = cliente.write_coil(MEMORIAS_PADRAO[nome_memoria], bool(estado))
            if resultado.isError():
                print(f"[CLP] Falha ao escrever {nome_memoria}: {resultado}")
                raise RuntimeError(f"Erro ao escrever {nome_memoria}: {resultado}")
            print(f"[CLP] Escrita OK em {nome_memoria}")
            return True
        finally:
            try:
                cliente.close()
            except Exception:
                pass

    def acionar_memoria(self, nome_memoria, pulso_seg=1):
        print(f"[CLP] Acionamento iniciado para {nome_memoria}")
        if not self.escrever_memoria(nome_memoria, True):
            print(f"[CLP] Acionamento recusado para {nome_memoria}")
            return False

        print(f"[CLP] {nome_memoria} mantida ligada aguardando reset manual")
        return True

    def resetar_memoria(self, nome_memoria):
        print(f"[CLP] Reset iniciado para {nome_memoria}")
        retorno = self.escrever_memoria(nome_memoria, False)
        print(f"[CLP] Reset {'concluido' if retorno else 'nao concluido'} para {nome_memoria}")
        return retorno