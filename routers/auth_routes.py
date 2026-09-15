from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from auth import verificar_senha, get_usuario_logado
from webtemplates import templates
import models

router = APIRouter()


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_usuario_logado(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "erro": None})


@router.post("/login")
def login_submit(request: Request, nip: str = Form(...), senha: str = Form(...),
                  db: Session = Depends(get_db)):
    usuario = db.get(models.Usuario, nip.strip())
    if not usuario or not usuario.ativo or not verificar_senha(senha, usuario.senha_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "erro": "NIP, senha inválidos ou usuário bloqueado."},
            status_code=401,
        )
    request.session["nip"] = usuario.nip
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
