import hmac
import hashlib
import re
from datetime import datetime, timezone

def validar_serial(serial):
    """Garante que o serial tenha 10 dígitos numéricos antes de processar."""
    return bool(re.match(r'^\d{10}$', serial))

def gerar_assinatura_hmac(sistema, chave_secreta, ruido, metodo, funcao):
    """
    Gera a chave conforme: sistema + data + método + ruído + função.
    Data formatada como: mês(2) + hora(2) + dia(2) + minuto(2) + ano(4) em UTC.
    """
    agora_utc = datetime.now(timezone.utc)
    data_formatada = agora_utc.strftime("%m%H%d%M%Y")
    
    mensagem = f"{sistema.lower()}{data_formatada}{metodo.upper()}{ruido}{funcao}"
    
    assinatura = hmac.new(
        chave_secreta.encode('utf-8'),
        mensagem.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return f"{sistema}:{assinatura}" # Formato exigido