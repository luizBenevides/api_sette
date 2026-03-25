import requests
from .seguranca import gerar_assinatura_hmac

class ClienteSpacecom:
    def __init__(self, config):
        self.url_base = config['URL_BASE']
        self.config = config

    def enviar_estanqueidade(self, serial, dados_mock):
        """Envia dados para /watertightness/log."""
        endpoint = "/watertightness/log"
        autorizacao = gerar_assinatura_hmac(
            self.config['SISTEMA'], self.config['CHAVE'], 
            self.config['RUIDO'], "POST", "log"
        )
        
        corpo = {
            "serial": serial,
            "name_jiga": self.config['JIGA_ESTANQUE'],
            "info": dados_mock # Aqui enviamos os valores de pressão
        }
        
        return self._post(endpoint, autorizacao, corpo)

    def _post(self, endpoint, auth, json_data):
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        try:
            r = requests.post(f"{self.url_base}{endpoint}", json=json_data, timeout=10)
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500