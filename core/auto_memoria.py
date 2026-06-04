"""Ciclo automatico de validacao das memorias do CLP.

O modo liga uma memoria por vez, aguarda a proxima serial para resetar e segue o ciclo.
Se o modo estiver desativado, o fluxo normal deve continuar sem interferencia.
"""

import os

from core.clp import MEMORIAS_PADRAO


class CicloAutomaticoMemorias:
    def __init__(self):
        self.ativo = os.getenv("AUTO_MEMORIA_MODO", "0").strip() == "1"
        ordem = os.getenv("AUTO_MEMORIA_ORDEM", "M130,M131,M132,M133")
        self.ordem = [item.strip() for item in ordem.split(",") if item.strip()]
        self.indice_atual = 0
        self.memoria_pendente = None

    def habilitado(self):
        return self.ativo and bool(self.ordem)

    def proxima_memoria(self):
        if not self.habilitado():
            return None
        memoria = self.ordem[self.indice_atual % len(self.ordem)]
        self.indice_atual += 1
        return memoria

    def registrar_ativacao(self, memoria):
        if memoria in MEMORIAS_PADRAO:
            self.memoria_pendente = memoria

    def possui_pendente(self):
        return self.memoria_pendente is not None

    def consumir_pendente(self):
        memoria = self.memoria_pendente
        self.memoria_pendente = None
        return memoria

    def processar_serial(self, serial_lido, controlador_clp, logger=print):
        """Executa o ciclo automatico completo para uma serial lida.

        Retorna True quando a serial foi consumida pelo ciclo automatico.
        Retorna False quando o fluxo normal deve seguir.
        """

        if not self.habilitado() or controlador_clp is None:
            return False

        if self.possui_pendente():
            memoria_pendente = self.consumir_pendente()
            if memoria_pendente:
                logger(f"[AUTO] Serial {serial_lido}: resetando {memoria_pendente} antes do proximo ciclo")
                try:
                    controlador_clp.resetar_memoria(memoria_pendente)
                except Exception as erro:
                    logger(f"[AUTO] Erro ao resetar {memoria_pendente}: {erro}")

        memoria = self.proxima_memoria()
        if not memoria:
            return False

        logger(f"[AUTO] Serial {serial_lido}: acionando {memoria}")
        try:
            if controlador_clp.acionar_memoria(memoria):
                self.registrar_ativacao(memoria)
                logger(f"[AUTO] {memoria} ligada e aguardando a proxima serial para reset")
                return True
        except Exception as erro:
            logger(f"[AUTO] Falha ao acionar {memoria}: {erro}")

        return False