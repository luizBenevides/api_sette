import psycopg2
import json
import os

class GerenciadorDeDados:
    def __init__(self, string_conexao):
        self.string_conexao = string_conexao

    def salvar_log(self, dados):
        try:
            conn = psycopg2.connect(self.string_conexao)
            cur = conn.cursor()
            query = """
                INSERT INTO logs_producao (serial, test_type, jiga_name, resultado, api_response_raw)
                VALUES (%s, %s, %s, %s, %s)
            """
            cur.execute(query, (
                dados['serial'], dados['tipo'], dados['jiga'], 
                dados['status'], json.dumps(dados['resposta'])
            ))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"Erro no Postgres: {e}. Salvando em TXT...")
            self.salvar_em_txt_emergencia(dados)
            return False

    def salvar_em_txt_emergencia(self, dados):
        with open("logs_emergencia.txt", "a", encoding="utf-8") as f:
            linha = f"{dados['serial']} | {dados['tipo']} | {dados['status']} | {json.dumps(dados['resposta'])}\n"
            f.write(linha)