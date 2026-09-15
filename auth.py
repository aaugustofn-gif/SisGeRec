from passlib.context import CryptContext
from fastapi import Request, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_senha(senha: str) -> str:
    return pwd_context.hash(senha)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return pwd_context.verify(senha, senha_hash)


def get_usuario_logado(request: Request, db: Session = Depends(get_db)):
    nip = request.session.get("nip")
    if not nip:
        return None
    usuario = db.get(models.Usuario, nip)
    if usuario and not usuario.ativo:
        return None
    return usuario


class NaoAutenticado(Exception):
    pass


class SenhaDeveSerTrocada(Exception):
    pass


def exigir_login(request: Request, db: Session = Depends(get_db)) -> models.Usuario:
    usuario = get_usuario_logado(request, db)
    if not usuario:
        raise NaoAutenticado()
    if usuario.deve_trocar_senha and request.url.path != "/trocar-senha":
        raise SenhaDeveSerTrocada()
    return usuario


def exigir_perfil(*perfis):
    def dependencia(usuario: models.Usuario = Depends(exigir_login)):
        if usuario.perfil not in perfis and usuario.perfil != "SUPERADMIN":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Acesso não autorizado para o seu perfil.")
        return usuario
    return dependencia
