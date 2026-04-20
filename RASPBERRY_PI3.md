# Rodando no Raspberry Pi 3B sem tela (headless)

## 1) Cenario e caminho real do projeto

Este passo a passo foi organizado para seu cenario:

- Raspberry Pi 3B 64-bit
- Sem monitor (headless)
- Leitor conectado por USB
- Projeto em /home/delta/scripts/api_sette

Para esse caso, use o arquivo principal_headless.py.

Observacao importante para seu leitor atual:

- Pelo log que voce enviou (USB HID Keyboard), o leitor funciona como teclado.
- Nesse caso nao usa /dev/ttyUSB0 nem /dev/ttyACM0.
- Use modo HID no .env (INPUT_MODE=hid).

## 2) Preparar sistema (pacotes base)

Atualize o Raspberry:

```bash
sudo apt update
sudo apt upgrade -y
```

Instale os pacotes necessarios para Python e Postgres client:

```bash
sudo apt install -y git python3 python3-pip python3-venv python3-dev libpq-dev postgresql postgresql-contrib
```

Para facilitar debug de dispositivos HID:

```bash
sudo apt install -y evtest
```

## 3) Configurar banco Postgres primeiro

### 3.1) Garantir que o Postgres esta ativo

```bash
sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo systemctl status postgresql
```

### 3.2) Criar usuario e banco (se ainda nao existir)

Exemplo padrao:

```bash
sudo -u postgres psql -c "CREATE USER sette_app WITH PASSWORD 'troque_senha_forte';"
sudo -u postgres psql -c "CREATE DATABASE log_sette OWNER sette_app;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE log_sette TO sette_app;"
```

Se usuario ou banco ja existirem, pule esta etapa.

### 3.3) Rodar seu script SQL de estrutura

entre no banco: 
    0 - sudo -u postgres psql -d log_sette -p 5433
rode essas queries 1 por 1 na ordem:
    1 - CREATE TYPE tipo_teste AS ENUM ('estanque', 'laser');
    2 - CREATE TYPE status_resultado AS ENUM ('A', 'R');
    3 - CREATE TABLE logs_producao (
            id BIGSERIAL PRIMARY KEY,
            serial CHAR(10) NOT NULL,
            test_type tipo_teste NOT NULL,
            jiga_name VARCHAR(50) NOT NULL,
            valor_estanqueidade NUMERIC(10, 2),
            unidade_medida VARCHAR(10),
            programa_teste VARCHAR(50),
            causa_falha VARCHAR(5),
            resultado status_resultado NOT NULL,
            enviado_api_externa BOOLEAN DEFAULT FALSE,
            http_status_retorno INTEGER,
            api_response_raw JSONB,
            criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT check_serial_numeric CHECK (serial ~ '^[0-9]{10}$')
        );
    4 - CREATE INDEX idx_logs_serial ON logs_producao(serial);
    5 - CREATE INDEX idx_logs_data ON logs_producao(criado_em);
    6 - GRANT USAGE ON SCHEMA public TO sette_app;
    7 - GRANT INSERT, SELECT, UPDATE ON TABLE public.logs_producao TO sette_app;
    8 - GRANT USAGE, SELECT ON SEQUENCE public.logs_producao_id_seq TO sette_app;
    9 - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT INSERT, SELECT, UPDATE ON TABLES TO sette_app;
    10 - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO sette_app;



### 3.4) Validar se a tabela esperada existe

```bash
sudo -u postgres psql -d log_sette -c "\dt"
sudo -u postgres psql -d log_sette -c "SELECT COUNT(*) FROM logs_producao;"
-- Ver quais clusters Postgres existem e em qual porta estão
sudo pg_lsclusters
sudo -u postgres psql -d log_sette -p 5433 -c "GRANT USAGE, SELECT ON SEQUENCE public.logs_producao_id_seq TO sette_app;"
PGPASSWORD='setteapp7771' psql -h localhost -p 5433 -U sette_app -d log_sette -c "insert into logs_producao (serial, test_type, jiga_name, resultado) values ('1234567890','estanque','TESTE','A');"

```

## 4) Configurar e instalar o software

Entre no diretorio do projeto:

```bash
cd /home/delta/scripts/api_sette
```

Crie ambiente virtual e instale dependencias headless:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requisitos_headless.txt
```

Se der erro no psycopg2-binary:

```bash
pip uninstall -y psycopg2-binary
pip install psycopg2 pyserial evdev
```

## 5) Configurar .env

Edite o arquivo .env com foco em banco + leitor:

```env
SPACECOM_SISTEMA=sette
SPACECOM_RUIDO=SEU_RUIDO
SPACECOM_CHAVE_API=SUA_CHAVE
URL_BASE_SPACECOM=https://sisgem-dev.spacecom.com.br/api/v2
NOME_JIGA=JIGA_SETTE_01

DB_URL=postgresql://sette_app:troque_senha_forte@localhost:5432/log_sette
ARQUIVO_EMERGENCIA=logs_emergencia.txt

INPUT_MODE=dual
SERIAL_PORT=/dev/serial0
SERIAL_BAUDRATE=9600
SERIAL_TIMEOUT=1
SERIAL_TERMINATOR=;
HID_DEVICE=/dev/input/by-id/usb-USBKey_Chip_USBKey_Module_202730041341-event-kbd(exemplo)

# Opcional, so se usar leitor serial de porta tty:
# INPUT_MODE=serial
# SERIAL_PORT=/dev/ttyUSB0
# SERIAL_BAUDRATE=9600
# SERIAL_TIMEOUT=1
```

## 6) Descobrir o device HID correto

Com o leitor conectado, liste os dispositivos de input:

```bash
ls -l /dev/input/by-id/
```

Procure a entrada com nome parecido com USBKey Module e pegue o link event:

```bash
readlink -f /dev/input/by-id/usb-USBKey_Chip_USBKey_Module-event-kbd
```

Exemplo de retorno:

```bash
/dev/input/event3
```

Use esse valor no .env em HID_DEVICE.

Se quiser validar teclas do leitor:

```bash
sudo evtest /dev/input/event3
```

No seu caso, o log ja indica perfil HID Keyboard.

Se em algum Raspberry futuro o leitor for serial real, ai sim use:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
dmesg | tail -n 50
```

Se necessario, ajuste INPUT_MODE e variaveis correspondentes no .env.

Se a ideia for receber o teste da G3i pela serial e depois ler o serial do produto pelo scanner, use:

```env
INPUT_MODE=dual
SERIAL_PORT=/dev/serial0
SERIAL_BAUDRATE=9600
SERIAL_TIMEOUT=1
SERIAL_TERMINATOR=;
HID_DEVICE=/dev/input/by-id/usb-USBKey_Chip_USBKey_Module_202730041341-event-kbd
```

Permissao para ler input HID (usuario delta):

```bash
sudo usermod -aG input delta
sudo usermod -aG input $USER
```

Permissao de porta serial (somente se usar tty):

```bash
sudo usermod -aG dialout delta
```

Depois reinicie o Raspberry para aplicar o grupo.

## 7) Teste manual antes do boot automatico

```bash
cd /home/delta/scripts/api_sette
source .venv/bin/activate
python principal_headless.py
```

Valide:

- Recebe leitura da maquina e do leitor
- Envia para API
- Grava no banco
- Em falha, salva em logs_emergencia.txt

## 8) Subir automatico no boot (systemd)

Crie o servico:

```bash
sudo nano /etc/systemd/system/api_sette_headless.service
```

Conteudo:

```ini
[Unit]
Description=API Sette Headless
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=delta
WorkingDirectory=/home/delta/scripts/api_sette
EnvironmentFile=/home/delta/scripts/api_sette/.env
ExecStart=/home/delta/scripts/api_sette/.venv/bin/python /home/delta/scripts/api_sette/principal_headless.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Ative e inicie:

```bash
sudo systemctl daemon-reload
sudo systemctl enable api_sette_headless.service
sudo systemctl start api_sette_headless.service
```

Verifique logs:

```bash
sudo systemctl status api_sette_headless.service
journalctl -u api_sette_headless.service -f
```

## 9) Checklist final

- Postgres instalado e ativo
- Banco log_sette criado
- Script SQL executado com sucesso
- DB_URL configurada no .env
- Dependencias instaladas no .venv
- INPUT_MODE configurado corretamente (hid ou serial)
- HID_DEVICE correto (quando HID)
- Permissoes de grupo ajustadas (input e/ou dialout)
- Servico systemd habilitado no boot
- Fluxo completo funcionando com leitura real
