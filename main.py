import os
from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import Base, engine, get_db, DATABASE_URL, SessionLocal
from auth import exigir_login, NaoAutenticado, SenhaDeveSerTrocada, hash_senha
import models
from routers import auth_routes, recursos, demandas, cem, status, admin, senha
from webtemplates import templates
from utils import processo_em_andamento, esta_atrasado, resumo_financeiro_por_nd

Base.metadata.create_all(bind=engine)


def migrar_esquema():
    """Adiciona colunas novas em bancos já existentes (SQLAlchemy create_all não altera tabelas
    existentes, só cria tabelas novas que ainda não existam)."""
    db = SessionLocal()
    try:
        if DATABASE_URL.startswith("mysql"):
            comandos = [
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS deve_trocar_senha BOOLEAN NOT NULL DEFAULT TRUE",
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS valor_unitario DECIMAL(14,2) NULL",
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS cancelada BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS data_cancelamento DATETIME NULL",
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS cancelado_por_nip VARCHAR(20) NULL",
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS motivo_cancelamento TEXT NULL",
                "ALTER TABLE status_config ADD COLUMN IF NOT EXISTS setor VARCHAR(100) NULL",
                "ALTER TABLE status_config ADD COLUMN IF NOT EXISTS prazo INT NULL",
                "ALTER TABLE demandas ADD COLUMN IF NOT EXISTS arquivada BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE linhas_status ADD COLUMN IF NOT EXISTS concluido BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE linhas_status ADD COLUMN IF NOT EXISTS data_conclusao DATETIME NULL",
            ]
            for cmd in comandos:
                db.execute(text(cmd))
            db.commit()
            # Preenche o valor unitário congelado para autorizações criadas antes desse campo existir
            db.execute(text(
                "UPDATE autorizacoes a JOIN demandas d ON a.demanda_id = d.id "
                "SET a.valor_unitario = d.valor_unitario WHERE a.valor_unitario IS NULL"
            ))
            db.commit()
        else:
            for comando in [
                "ALTER TABLE usuarios ADD COLUMN deve_trocar_senha BOOLEAN NOT NULL DEFAULT 1",
                "ALTER TABLE autorizacoes ADD COLUMN valor_unitario DECIMAL(14,2) NULL",
                "ALTER TABLE autorizacoes ADD COLUMN cancelada BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE autorizacoes ADD COLUMN data_cancelamento DATETIME NULL",
                "ALTER TABLE autorizacoes ADD COLUMN cancelado_por_nip VARCHAR(20) NULL",
                "ALTER TABLE autorizacoes ADD COLUMN motivo_cancelamento TEXT NULL",
                "ALTER TABLE status_config ADD COLUMN setor VARCHAR(100) NULL",
                "ALTER TABLE status_config ADD COLUMN prazo INTEGER NULL",
                "ALTER TABLE demandas ADD COLUMN arquivada BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE linhas_status ADD COLUMN concluido BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE linhas_status ADD COLUMN data_conclusao DATETIME NULL",
            ]:
                try:
                    db.execute(text(comando))
                    db.commit()
                except Exception:
                    db.rollback()
            try:
                db.execute(text(
                    "UPDATE autorizacoes SET valor_unitario = "
                    "(SELECT valor_unitario FROM demandas WHERE demandas.id = autorizacoes.demanda_id) "
                    "WHERE valor_unitario IS NULL"
                ))
                db.commit()
            except Exception:
                db.rollback()
    finally:
        db.close()


migrar_esquema()

app = FastAPI(title="SisGeRec - Sistema de Gestão de Recursos")

SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=60 * 60 * 12)


@app.middleware("http")
async def sem_cache(request: Request, call_next):
    """Evita que o navegador (especialmente Safari/iPad) sirva páginas em cache com saldos/status
    desatualizados após uma ratificação, cancelamento ou mudança de status."""
    response = await call_next(request)
    if request.url.path != "/static" and not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth_routes.router)
app.include_router(senha.router)
app.include_router(recursos.router)
app.include_router(demandas.router)
app.include_router(cem.router)
app.include_router(status.router)
app.include_router(admin.router)


@app.exception_handler(NaoAutenticado)
async def handler_nao_autenticado(request: Request, exc: NaoAutenticado):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(SenhaDeveSerTrocada)
async def handler_senha_deve_ser_trocada(request: Request, exc: SenhaDeveSerTrocada):
    return RedirectResponse("/trocar-senha", status_code=303)


@app.on_event("startup")
def criar_superadmin_inicial():
    db: Session = next(get_db())
    try:
        if db.query(models.Usuario).count() == 0:
            nip = os.environ.get("SUPERADMIN_NIP")
            senha = os.environ.get("SUPERADMIN_SENHA")
            if nip and senha:
                db.add(models.Usuario(
                    nip=nip, posto=os.environ.get("SUPERADMIN_POSTO", "CF"),
                    nome=os.environ.get("SUPERADMIN_NOME", "Administrador"),
                    setor=os.environ.get("SUPERADMIN_SETOR", "G30"),
                    perfil="SUPERADMIN", senha_hash=hash_senha(senha), ativo=True,
                ))
                db.commit()
    finally:
        db.close()


@app.get("/")
def dashboard(request: Request, usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    total_demandas_pendentes = sum(
        1 for d in db.query(models.Demanda).all()
        if not d.arquivada and d.quantidade_pendente() > 0
    )

    linhas = db.query(models.LinhaStatus).all()
    processos_em_andamento = sum(1 for l in linhas if processo_em_andamento(db, l))
    processos_atrasados = sum(1 for l in linhas if esta_atrasado(db, l))

    resumo_nd = resumo_financeiro_por_nd(db)

    total_usuarios = db.query(models.Usuario).count()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "usuario": usuario,
        "total_demandas_pendentes": total_demandas_pendentes,
        "processos_em_andamento": processos_em_andamento,
        "processos_atrasados": processos_atrasados,
        "resumo_nd": resumo_nd,
        "total_usuarios": total_usuarios,
    })
