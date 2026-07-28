"""Regras reais para acionar memorias do CLP com base em serial e historico."""

from datetime import datetime, timezone
import os
import re

import psycopg2
from core.database import conectar_banco


class AvaliadorCasosMemoria:
    def __init__(self):
        self.minutos_reteste = int(os.getenv("RETESTE_MINUTOS_MINIMO", "30"))

    def _serial_valida(self, serial_lido):
        return bool(re.fullmatch(r"\d{10}", serial_lido or ""))

    def _buscar_historico(self, serial_lido):
        conn = conectar_banco()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT resultado, criado_em
                FROM logs_producao
                WHERE serial = %s
                  AND tratamento_clp IS NULL
                ORDER BY criado_em DESC
                LIMIT 50
                """,
                (serial_lido,),
            )
            linhas = cur.fetchall()
            cur.close()
            return linhas
        finally:
            conn.close()

    def avaliar(self, serial_lido):
        """Retorna (memoria_alvo, motivo) ou (None, None) quando nao cair em caso."""
        if not self._serial_valida(serial_lido):
            return "M130", "serial_sem_integracao"

        try:
            historico = self._buscar_historico(serial_lido)
        except Exception:
            # Sem acesso ao banco, nao bloqueia o fluxo automaticamente.
            return None, None

        if not historico:
            return None, None

        total_reprovacoes = sum(1 for resultado, _ in historico if resultado == "R")
        if total_reprovacoes >= 2:
            return "M133", "bloqueio_reteste_2_reprovacoes"

        if any(resultado == "A" for resultado, _ in historico):
            return "M131", "produto_ja_aprovado_ou_finalizado"

        resultado_mais_recente, data_mais_recente = historico[0]
        if resultado_mais_recente == "R" and data_mais_recente is not None:
            agora = datetime.now(timezone.utc)
            if data_mais_recente.tzinfo is None:
                data_mais_recente = data_mais_recente.replace(tzinfo=timezone.utc)

            minutos_passados = (agora - data_mais_recente).total_seconds() / 60.0
            if minutos_passados < self.minutos_reteste:
                return "M132", "reteste_antes_janela_30_min"

        return None, None
