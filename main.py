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

Base.metadata.create_all(bind=engine)


def migrar_esquema():
    """Adiciona colunas novas em bancos já existentes (SQLAlchemy create_all não altera tabelas existentes)."""
    db = SessionLocal()
    try:
        if DATABASE_URL.startswith("mysql"):
            db.execute(text(
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS "
                "deve_trocar_senha BOOLEAN NOT NULL DEFAULT TRUE"
            ))
            db.execute(text(
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS valor_unitario DECIMAL(14,2) NULL"
            ))
            db.execute(text(
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS cancelada BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            db.execute(text(
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS data_cancelamento DATETIME NULL"
            ))
            db.execute(text(
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS cancelado_por_nip VARCHAR(20) NULL"
            ))
            db.execute(text(
                "ALTER TABLE autorizacoes ADD COLUMN IF NOT EXISTS motivo_cancelamento TEXT NULL"
            ))
            db.execute(text(
                "ALTER TABLE status_config ADD COLUMN IF NOT EXISTS setor VARCHAR(20) NULL"
            ))
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
                "ALTER TABLE status_config ADD COLUMN setor VARCHAR(20) NULL",
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
    """Cria o primeiro SUPERADMIN a partir de variáveis de ambiente, se ainda não existir nenhum usuário."""
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
        1 for d in db.query(models.Demanda).all() if d.quantidade_pendente() > 0
    )
    total_usuarios = db.query(models.Usuario).count()
    total_linhas_status = db.query(models.LinhaStatus).count()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "usuario": usuario,
        "total_demandas_pendentes": total_demandas_pendentes,
        "total_usuarios": total_usuarios,
        "total_linhas_status": total_linhas_status,
    })
