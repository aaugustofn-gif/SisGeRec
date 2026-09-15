import secrets
from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database import get_db
from auth import exigir_perfil, hash_senha
import models
from webtemplates import templates

router = APIRouter()


@router.get("/admin/usuarios")
def listar_usuarios(request: Request, nova_senha: str = None, nip_reset: str = None,
                     erro_exclusao: str = None,
                     usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    usuarios = db.query(models.Usuario).order_by(models.Usuario.nome).all()
    return templates.TemplateResponse("admin_usuarios.html", {
        "request": request, "usuario": usuario, "usuarios": usuarios,
        "nova_senha": nova_senha, "nip_reset": nip_reset, "erro_exclusao": erro_exclusao,
    })


@router.get("/admin/usuarios/novo")
def novo_usuario_form(request: Request, usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    perfis = models.PERFIL_CHOICES if usuario.perfil == "SUPERADMIN" else ["COMUM", "CEM"]
    return templates.TemplateResponse("usuario_form.html", {
        "request": request, "usuario": usuario, "setor_choices": models.SETOR_CHOICES,
        "perfil_choices": perfis, "editando": None, "erro": None, "senha_gerada": None,
    })


@router.post("/admin/usuarios/novo")
def criar_usuario(request: Request, nip: str = Form(...), posto: str = Form(...), nome: str = Form(...),
                   setor: str = Form(...), perfil: str = Form(...),
                   usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    if usuario.perfil != "SUPERADMIN" and perfil in ("SUPERADMIN", "ADMIN"):
        perfil = "COMUM"

    if db.get(models.Usuario, nip.strip()):
        perfis = models.PERFIL_CHOICES if usuario.perfil == "SUPERADMIN" else ["COMUM", "CEM"]
        return templates.TemplateResponse("usuario_form.html", {
            "request": request, "usuario": usuario, "setor_choices": models.SETOR_CHOICES,
            "perfil_choices": perfis, "editando": None, "erro": "Já existe usuário com esse NIP.",
            "senha_gerada": None,
        }, status_code=400)

    senha_provisoria = secrets.token_urlsafe(6)
    novo = models.Usuario(
        nip=nip.strip(), posto=posto, nome=nome, setor=setor, perfil=perfil,
        senha_hash=hash_senha(senha_provisoria), ativo=True,
    )
    db.add(novo)
    db.commit()

    perfis = models.PERFIL_CHOICES if usuario.perfil == "SUPERADMIN" else ["COMUM", "CEM"]
    return templates.TemplateResponse("usuario_form.html", {
        "request": request, "usuario": usuario, "setor_choices": models.SETOR_CHOICES,
        "perfil_choices": perfis, "editando": None, "erro": None,
        "senha_gerada": senha_provisoria, "nip_criado": novo.nip,
    })


@router.get("/admin/usuarios/{nip}/editar")
def editar_usuario_form(nip: str, request: Request, usuario=Depends(exigir_perfil("ADMIN")),
                         db: Session = Depends(get_db)):
    alvo = db.get(models.Usuario, nip)
    if not alvo:
        return RedirectResponse("/admin/usuarios", status_code=303)
    if usuario.perfil != "SUPERADMIN" and alvo.perfil in ("SUPERADMIN", "ADMIN"):
        return RedirectResponse("/admin/usuarios", status_code=303)

    perfis = models.PERFIL_CHOICES if usuario.perfil == "SUPERADMIN" else ["COMUM", "CEM"]
    return templates.TemplateResponse("usuario_form.html", {
        "request": request, "usuario": usuario, "setor_choices": models.SETOR_CHOICES,
        "perfil_choices": perfis, "editando": alvo, "erro": None, "senha_gerada": None,
    })


@router.post("/admin/usuarios/{nip}/editar")
def editar_usuario(nip: str, posto: str = Form(...), nome: str = Form(...),
                    setor: str = Form(...), perfil: str = Form(...),
                    usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    alvo = db.get(models.Usuario, nip)
    if not alvo:
        return RedirectResponse("/admin/usuarios", status_code=303)

    if usuario.perfil != "SUPERADMIN":
        if alvo.perfil in ("SUPERADMIN", "ADMIN"):
            return RedirectResponse("/admin/usuarios", status_code=303)
        if perfil in ("SUPERADMIN", "ADMIN"):
            perfil = alvo.perfil

    alvo.posto = posto
    alvo.nome = nome
    alvo.setor = setor
    alvo.perfil = perfil
    db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/admin/usuarios/{nip}/resetar-senha")
def resetar_senha(nip: str, usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    alvo = db.get(models.Usuario, nip)
    nova_senha = None
    if alvo:
        nova_senha = secrets.token_urlsafe(6)
        alvo.senha_hash = hash_senha(nova_senha)
        alvo.deve_trocar_senha = True
        db.commit()
    destino = f"/admin/usuarios?nova_senha={nova_senha}&nip_reset={nip}" if nova_senha else "/admin/usuarios"
    return RedirectResponse(destino, status_code=303)


@router.post("/admin/usuarios/{nip}/bloquear")
def bloquear_usuario(nip: str, usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    alvo = db.get(models.Usuario, nip)
    if alvo:
        alvo.ativo = not alvo.ativo
        db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/admin/usuarios/{nip}/excluir")
def excluir_usuario(nip: str, usuario=Depends(exigir_perfil("SUPERADMIN")), db: Session = Depends(get_db)):
    alvo = db.get(models.Usuario, nip)
    erro = None
    if alvo:
        try:
            db.delete(alvo)
            db.commit()
        except IntegrityError:
            db.rollback()
            erro = (
                "Não é possível excluir este usuário porque ele já tem registros vinculados "
                "no sistema (recursos, demandas, autorizações ou histórico de status). "
                "Em vez de excluir, edite o perfil dele ou bloqueie o acesso."
            )
    destino = "/admin/usuarios"
    if erro:
        destino += f"?erro_exclusao={quote(erro)}"
    return RedirectResponse(destino, status_code=303)
