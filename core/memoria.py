"""Memoria operacional compartilhada para UI, automacao e futura integracao com CLP."""

from copy import deepcopy
import os

from core.clp import MEMORIAS_PADRAO


TELAS_AUTOMACAO = [
    {
        "id": "serial_sem_integracao",
        "titulo": "Serial sem integracao",
        "gatilho": "serial lida nao for aceita ou nao estiver valida",
        "acao": "bloquear o fluxo e exibir o motivo da rejeicao",
    },
    {
        "id": "produto_aprovado_ou_finalizado",
        "titulo": "Produto ja aprovado ou finalizado",
        "gatilho": "serial ja processada e aprovada em uma tentativa anterior",
        "acao": "impedir retrabalho e orientar o operador a seguir para a proxima peca",
    },
    {
        "id": "reteste_antes_30_minutos",
        "titulo": "Reteste antes dos 30 minutos",
        "gatilho": "mesma serial reprovada e nova tentativa antes de 30 minutos",
        "acao": "bloquear o reteste ate completar a janela minima",
    },
    {
        "id": "bloqueio_reteste_2_falhas",
        "titulo": "Bloqueio de reteste apos 2 falhas",
        "gatilho": "peca reprovada 2 vezes",
        "acao": "nao permitir novo reteste para esta serial",
    },
]


VARIAVEIS_MEMORIA_SUGERIDAS = [
    "CLP_TRANSPORTE=ethernet",
    "CLP_PROTOCOLO=modbus_tcp",
    "CLP_IP=",
    "CLP_PORTA_TCP=502",
    "CLP_INTERFACE_REDE=eth0",
    "CLP_TIMEOUT_SEG=3",
    "RASPBERRY_IP=",
    "RETESTE_MINUTOS_MINIMO=30",
    "RETESTE_FALHAS_MAXIMAS=2",
    "STATUS_MEMORIA_ABERTA=1",
]


def _int_env(nome_variavel, valor_padrao):
    valor = os.getenv(nome_variavel, str(valor_padrao)).strip()
    try:
        return int(valor)
    except ValueError:
        return valor_padrao


def obter_memoria_operacional():
    """Retorna um snapshot da memoria operacional com os placeholders do projeto."""
    return {
        "conexao_clp": {
            "transporte": os.getenv("CLP_TRANSPORTE", "ethernet"),
            "protocolo": os.getenv("CLP_PROTOCOLO", "tcp"),
            "ip_clp": os.getenv("CLP_IP", ""),
            "porta_tcp": _int_env("CLP_PORTA_TCP", 502),
            "interface_rede": os.getenv("CLP_INTERFACE_REDE", "eth0"),
            "timeout_seg": _int_env("CLP_TIMEOUT_SEG", 3),
            "ip_raspberry": os.getenv("RASPBERRY_IP", ""),
        },
        "regras_reteste": {
            "minutos_entre_retestes": _int_env("RETESTE_MINUTOS_MINIMO", 30),
            "limite_falhas": _int_env("RETESTE_FALHAS_MAXIMAS", 2),
        },
        "campos_memoria": {
            "serial_atual": "",
            "status_teste": "",
            "resultado_ultimo_teste": "",
            "falhas_mesma_peca": 0,
            "ultimo_teste_em": None,
            "bloqueio_reteste_ate": None,
            "conexao_clp_ok": False,
        },
        "telas_automacao": deepcopy(TELAS_AUTOMACAO),
        "mapeamento_memorias_clp": dict(MEMORIAS_PADRAO),
        "variaveis_sugeridas": list(VARIAVEIS_MEMORIA_SUGERIDAS),
    }