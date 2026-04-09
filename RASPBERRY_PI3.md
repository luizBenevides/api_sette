# Rodando no Raspberry Pi 3B sem tela (headless)

## 1) Seu cenario (Raspberry Pi 3B 64-bit, sem monitor)

Com esse hardware, o melhor caminho e rodar em modo headless:

- CPU: quad-core Cortex-A53
- RAM: 1 GB DDR2
- Sem tela/desktop
- Leitor serial fisico conectado ao Raspberry

Importante:

- O `principal.py` atual e grafico (PySide6) e nao e a melhor opcao para esse cenario.
- Para headless, use `principal_headless.py` (incluido neste projeto).

## 2) O que usar no modo headless

Dependencias identificadas no projeto:

- requests
- psycopg2-binary
- python-dotenv
- pyserial

Arquivo principal para headless:

- principal_headless.py
- requisitos_headless.txt

## 3) Preparar o Raspberry Pi 3B

Atualize o sistema:

```bash
sudo apt update
sudo apt upgrade -y
```

Instale pacotes de base:

```bash
sudo apt install -y git python3 python3-pip python3-venv libpq-dev python3-dev
```

Observacao:

- libpq-dev e python3-dev ajudam caso o psycopg2 precise compilar.

## 4) Levar o projeto para o Pi

Opcao A (git):

```bash
git clone <URL_DO_REPOSITORIO>
cd api_sette
```

Opcao B (copiar pasta por SCP do Windows para o Pi):

```bash
scp -r C:/caminho/api_sette pi@IP_DO_PI:/home/pi/
```

Depois:

```bash
cd /home/pi/api_sette
```

## 5) Criar ambiente virtual e instalar dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requisitos_headless.txt
```

Se der erro no psycopg2-binary, tente:

```bash
pip uninstall -y psycopg2-binary
pip install psycopg2 pyserial
```

## 6) Configurar o .env no Raspberry

Crie ou edite o arquivo .env na raiz do projeto:

```env
SPACECOM_SISTEMA=sette
SPACECOM_RUIDO=SEU_RUIDO
SPACECOM_CHAVE_API=SUA_CHAVE
URL_BASE_SPACECOM=https://sisgem-dev.spacecom.com.br/api/v2
NOME_JIGA=JIGA_SETTE_01
DB_URL=postgresql://usuario:senha@host:5432/log_sette
ARQUIVO_EMERGENCIA=logs_emergencia.txt

# Leitor serial
SERIAL_PORT=/dev/ttyUSB0
SERIAL_BAUDRATE=9600
SERIAL_TIMEOUT=1
```

Importante:

- Nao versionar o .env com segredos.
- Se voce abriu ou compartilhou a chave atual, gere uma nova chave (rotacao).

## 7) Descobrir a porta correta do leitor

Conecte o leitor e rode:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Se quiser, confirme no log do kernel:

```bash
dmesg | tail -n 50
```

Atualize `SERIAL_PORT` no `.env` se necessario.

## 8) Rodar a aplicacao (headless)

Com o ambiente virtual ativo:

```bash
python principal_headless.py
```

## 9) Testes rapidos apos subir

- Verifique no terminal logs de inicializacao do modo headless.
- Passe um serial no leitor.
- Confirme envio para API.
- Confirme gravacao no Postgres.
- Se banco/API falhar, verificar se escreveu em logs_emergencia.txt.

## 10) Subir automatico no boot (systemd)

Crie o arquivo de servico:

```bash
sudo nano /etc/systemd/system/api_sette_headless.service
```

Conteudo do servico:

```ini
[Unit]
Description=API Sette Headless
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/api_sette
EnvironmentFile=/home/pi/api_sette/.env
ExecStart=/home/pi/api_sette/.venv/bin/python /home/pi/api_sette/principal_headless.py
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

Ver status/logs:

```bash
sudo systemctl status api_sette_headless.service
journalctl -u api_sette_headless.service -f
```

## 10) Troubleshooting rapido

## 11) Troubleshooting rapido

- Nao le da serial: confirme `SERIAL_PORT` e permissao do usuario no grupo `dialout`.
- Para liberar porta serial para usuario `pi`:

```bash
sudo usermod -aG dialout pi
```

Depois reinicie o Raspberry.

- Erro no banco: validar DB_URL, rede e tabela logs_producao.
- Erro de auth na Spacecom: revisar sistema/chave/ruido e formato da assinatura.

## 12) Checklist final

- Raspberry Pi OS 64-bit instalado.
- Projeto copiado para /home/pi/api_sette.
- .venv criado e dependencias instaladas.
- .env configurado com dados corretos.
- Leitor serial detectado na porta correta.
- Servico systemd ativo no boot.
- Aplicacao processou serial e enviou para API.
- Fallback em logs_emergencia.txt validado.
