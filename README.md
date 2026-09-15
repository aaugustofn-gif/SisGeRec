# SisGeRec — Sistema de Gestão de Recursos (ComDivRib)

Painel logístico para controlar recursos (por ND/Origem), demandas, autorização do CEM,
acompanhamento do processo de aquisição (com prazos e observações) e resumo financeiro por ND.

Stack: FastAPI + SQLAlchemy + TiDB Cloud (MySQL-compatible) + Render.

## Deploy

1. TiDB Cloud: crie um cluster Serverless grátis e um banco `sisgerec`.
2. Monte a `DATABASE_URL`: `mysql+pymysql://USUARIO:SENHA@HOST:4000/sisgerec`
   (sem parâmetros ssl_verify_* — o TLS é configurado no próprio código via certifi).
3. Suba esta pasta para um repositório no GitHub (arquivos na raiz, não em subpasta).
4. No Render, crie um Web Service apontando pro repositório.
5. Em Settings, confira/preencha manualmente (o Render nem sempre lê o render.yaml
   automaticamente em serviços criados fora do fluxo "Blueprint"):
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Environment**: `DATABASE_URL`, `SECRET_KEY`, `SUPERADMIN_NIP`, `SUPERADMIN_SENHA`,
     `SUPERADMIN_POSTO`, `SUPERADMIN_NOME`, `SUPERADMIN_SETOR`
6. Deploy. As tabelas são criadas automaticamente no primeiro start, e migrações de colunas
   novas em tabelas já existentes rodam automaticamente também (função `migrar_esquema()`
   em `main.py`).
7. Configure um ping em cron-job.org para a URL do serviço (a cada ~10 min) para evitar que
   o plano gratuito do Render suspenda a aplicação por inatividade.

## Primeiro uso

1. Entre como SUPERADMIN (dados definidos nas variáveis de ambiente SUPERADMIN_*).
2. Troque a senha (obrigatório no primeiro acesso).
3. Cadastre as Origens de recurso.
4. Lance os Recursos iniciais.
5. Cadastre os demais usuários em Admin > Usuários.
6. Em Config. de Status, cadastre a sequência de status de cada tipo de processo — use
   exatamente os nomes "Empenhado" e "Liquidado" nos passos correspondentes, para que o
   quadro de recursos por ND do Início classifique os valores corretamente. Defina também
   o setor responsável e o prazo (em dias) de cada passo, se quiser o alerta de atraso.

## Rodar localmente (opcional)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Sem `DATABASE_URL` definida, usa um SQLite local (`sisgerec_local.db`) — só para testes.
