"""Configuracao centralizada do PostgreSQL a partir do arquivo .env."""

import os

import psycopg2


def parametros_conexao():
    db_url = os.getenv("DB_URL", "").strip()
    if db_url:
        return db_url

    obrigatorios = {
        "host": os.getenv("DB_HOST", "").strip(),
        "dbname": os.getenv("DB_NAME", "").strip(),
        "user": os.getenv("DB_USER", "").strip(),
        "password": os.getenv("DB_PASS", ""),
    }
    faltando = [nome for nome, valor in obrigatorios.items() if not valor]
    if faltando:
        raise RuntimeError(f"Parametros de banco ausentes: {', '.join(faltando)}")
    obrigatorios["port"] = int(os.getenv("DB_PORT", "5432"))
    obrigatorios["connect_timeout"] = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
    return obrigatorios


def conectar_banco():
    parametros = parametros_conexao()
    if isinstance(parametros, str):
        return psycopg2.connect(parametros)
    return psycopg2.connect(**parametros)


def testar_banco():
    conn = conectar_banco()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() == (1,)
    finally:
        conn.close()
