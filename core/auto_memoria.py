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
        self.memoria_pendente = None

    def habilitado(self):
        return self.ativo and bool(self.ordem)

    def registrar_ativacao(self, memoria):
        if memoria in MEMORIAS_PADRAO:
            self.memoria_pendente = memoria

    def possui_pendente(self):
        return self.memoria_pendente is not None

    def consumir_pendente(self):
        memoria = self.memoria_pendente
        self.memoria_pendente = None
        return memoria

    def processar_serial(self, serial_lido, controlador_clp, logger=print, memoria_alvo=None):
        """Executa apenas o que estiver explicitamente pendente ou solicitado.

        Evita resetar se a memoria alvo for a mesma que ja esta ligada.
        """

        if not self.habilitado() or controlador_clp is None:
            return False

        # Se ja temos uma memoria ligada e a nova memoria_alvo e a mesma, nao faz nada.
        if self.memoria_pendente == memoria_alvo and memoria_alvo is not None:
            logger(f"[AUTO] Serial {serial_lido}: {memoria_alvo} ja esta ativa. Mantendo.")
            return False

        # Se temos uma memoria ligada e a nova e diferente (ou None), precisamos resetar a atual.
        if self.possui_pendente():
            memoria_antiga = self.consumir_pendente()
            if memoria_antiga:
                logger(f"[AUTO] Serial {serial_lido}: resetando {memoria_antiga} para liberar/trocar")
                try:
                    controlador_clp.resetar_memoria(memoria_antiga)
                except Exception as erro:
                    logger(f"[AUTO] Erro ao resetar {memoria_antiga}: {erro}")

        # Se agora temos uma nova memoria para ligar, ligamos.
        if memoria_alvo is None:
            return False

        if memoria_alvo not in MEMORIAS_PADRAO:
            logger(f"[AUTO] Memoria alvo invalida: {memoria_alvo}")
            return False

        logger(f"[AUTO] Serial {serial_lido}: acionando {memoria_alvo}")
        try:
            if controlador_clp.acionar_memoria(memoria_alvo):
                self.registrar_ativacao(memoria_alvo)
                return True
        except Exception as erro:
            logger(f"[AUTO] Falha ao acionar {memoria_alvo}: {erro}")

        return False