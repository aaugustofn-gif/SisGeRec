from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from decimal import Decimal, InvalidOperation
from database import get_db
from auth import exigir_login
from utils import gerar_xlsx, int_ou_none
import models
from webtemplates import templates

router = APIRouter()


@router.get("/demandas")
def listar_demandas(request: Request, nd: str = None, origem_id: str = None, setor: str = None,
                     mostrar_arquivadas: str = None,
                     usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    origem_id = int_ou_none(origem_id)
    q = db.query(models.Demanda)
    if nd:
        q = q.filter(models.Demanda.nd == nd)
    if origem_id:
        q = q.filter(models.Demanda.origem_desejada_id == origem_id)
    if setor:
        q = q.filter(models.Demanda.setor == setor)
    if not mostrar_arquivadas:
        q = q.filter(models.Demanda.arquivada == False)
    demandas = q.order_by(models.Demanda.data_cadastro.desc()).all()
    origens = db.query(models.Origem).filter(models.Origem.ativo == True).order_by(models.Origem.nome).all()

    return templates.TemplateResponse("demandas.html", {
        "request": request, "usuario": usuario, "demandas": demandas, "origens": origens,
        "nd_choices": models.ND_CHOICES, "setor_choices": models.SETOR_CHOICES,
        "filtro_nd": nd, "filtro_origem": origem_id, "filtro_setor": setor,
        "mostrar_arquivadas": mostrar_arquivadas,
    })


@router.get("/demandas/exportar")
def exportar_demandas(nd: str = None, origem_id: str = None, setor: str = None,
                       mostrar_arquivadas: str = None,
                       usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    origem_id = int_ou_none(origem_id)
    q = db.query(models.Demanda)
    if nd:
        q = q.filter(models.Demanda.nd == nd)
    if origem_id:
        q = q.filter(models.Demanda.origem_desejada_id == origem_id)
    if setor:
        q = q.filter(models.Demanda.setor == setor)
    if not mostrar_arquivadas:
        q = q.filter(models.Demanda.arquivada == False)
    demandas = q.order_by(models.Demanda.data_cadastro.desc()).all()

    headers = ["Data", "Descrição", "ND", "Qtd. solicitada", "Vlr Unit. (R$)", "Vlr Total (R$)",
               "Origem desejada", "Qtd. autorizada", "Status geral", "Setor", "Responsável", "Observações"]
    linhas = [
        [d.data_cadastro.strftime("%d/%m/%Y"), d.descricao, d.nd, d.quantidade,
         float(d.valor_unitario), d.valor_total(),
         d.origem_desejada.nome if d.origem_desejada else "", d.quantidade_autorizada_acumulada(),
         d.status_geral(), d.setor, d.militar_responsavel.nome_completo(), d.observacoes or ""]
        for d in demandas
    ]
    buffer = gerar_xlsx(headers, linhas, "Demandas")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=demandas_sisgerec.xlsx"},
    )


@router.get("/demandas/nova")
def nova_demanda_form(request: Request, usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    origens = db.query(models.Origem).filter(models.Origem.ativo == True).order_by(models.Origem.nome).all()
    return templates.TemplateResponse("demanda_form.html", {
        "request": request, "usuario": usuario, "origens": origens,
        "nd_choices": models.ND_CHOICES, "erro": None,
    })


@router.post("/demandas/nova")
def criar_demanda(request: Request, descricao: str = Form(...), quantidade: int = Form(...),
                   nd: str = Form(...), valor_unitario: str = Form(...),
                   origem_desejada_id: str = Form(""), observacoes: str = Form(""),
                   usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    try:
        valor_dec = Decimal(valor_unitario.replace(",", "."))
        if quantidade <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        origens = db.query(models.Origem).filter(models.Origem.ativo == True).all()
        return templates.TemplateResponse("demanda_form.html", {
            "request": request, "usuario": usuario, "origens": origens,
            "nd_choices": models.ND_CHOICES, "erro": "Quantidade ou valor unitário inválido.",
        }, status_code=400)

    demanda = models.Demanda(
        descricao=descricao, quantidade=quantidade, nd=nd, valor_unitario=valor_dec,
        origem_desejada_id=int(origem_desejada_id) if origem_desejada_id else None,
        militar_responsavel_nip=usuario.nip, setor=usuario.setor, observacoes=observacoes,
    )
    db.add(demanda)
    db.commit()
    return RedirectResponse("/demandas", status_code=303)


def _pode_editar(usuario, demanda) -> bool:
    return usuario.perfil in ("ADMIN", "SUPERADMIN") or usuario.nip == demanda.militar_responsavel_nip


@router.get("/demandas/{demanda_id}/editar")
def editar_demanda_form(demanda_id: int, request: Request, usuario=Depends(exigir_login),
                         db: Session = Depends(get_db)):
    demanda = db.get(models.Demanda, demanda_id)
    if not demanda or not _pode_editar(usuario, demanda):
        return RedirectResponse("/demandas", status_code=303)

    origens = db.query(models.Origem).filter(models.Origem.ativo == True).order_by(models.Origem.nome).all()
    bloqueado = len(demanda.autorizacoes_ativas()) > 0  # já tem autorização ativa: trava só a ND
    return templates.TemplateResponse("demanda_form.html", {
        "request": request, "usuario": usuario, "origens": origens,
        "nd_choices": models.ND_CHOICES, "erro": None, "demanda": demanda, "bloqueado": bloqueado,
    })


@router.post("/demandas/{demanda_id}/editar")
def editar_demanda(demanda_id: int, request: Request, descricao: str = Form(...),
                    quantidade: int = Form(...), nd: str = Form(None), valor_unitario: str = Form(...),
                    origem_desejada_id: str = Form(""), observacoes: str = Form(""),
                    usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    demanda = db.get(models.Demanda, demanda_id)
    if not demanda or not _pode_editar(usuario, demanda):
        return RedirectResponse("/demandas", status_code=303)

    bloqueado = len(demanda.autorizacoes_ativas()) > 0
    origens = db.query(models.Origem).filter(models.Origem.ativo == True).order_by(models.Origem.nome).all()

    ja_autorizado = demanda.quantidade_autorizada_acumulada()
    erro = None
    valor_dec = None
    if quantidade <= 0 or quantidade < ja_autorizado:
        erro = f"Quantidade não pode ser menor do que a já autorizada ({ja_autorizado})."
    else:
        try:
            valor_dec = Decimal(valor_unitario.replace(",", "."))
        except (InvalidOperation, AttributeError):
            erro = "Valor unitário inválido."

    if erro:
        return templates.TemplateResponse("demanda_form.html", {
            "request": request, "usuario": usuario, "origens": origens,
            "nd_choices": models.ND_CHOICES, "erro": erro, "demanda": demanda, "bloqueado": bloqueado,
        }, status_code=400)

    demanda.descricao = descricao
    demanda.quantidade = quantidade
    demanda.origem_desejada_id = int(origem_desejada_id) if origem_desejada_id else None
    demanda.observacoes = observacoes
    demanda.valor_unitario = valor_dec  # editável mesmo após autorizada (preço final da licitação)
    if not bloqueado:
        demanda.nd = nd

    # Propaga o novo valor unitário para as autorizações ATIVAS já ratificadas — assim o saldo em
    # Recursos e o valor mostrado no painel de Status refletem o preço final da licitação/pesquisa.
    # Autorizações CANCELADAS mantêm o valor histórico com que foram efetivamente processadas.
    for a in demanda.autorizacoes_ativas():
        a.valor_unitario = valor_dec

    db.commit()
    return RedirectResponse(f"/demandas/{demanda.id}", status_code=303)


@router.post("/demandas/{demanda_id}/excluir")
def excluir_demanda(demanda_id: int, usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    """'Excluir' aqui arquiva a demanda (some das listagens), em vez de apagar o registro do banco.
    Excluir de fato quebraria o histórico de recursos/status já vinculados a ela. Pode ser
    restaurada a qualquer momento em /demandas/{id}/restaurar."""
    demanda = db.get(models.Demanda, demanda_id)
    if demanda and _pode_editar(usuario, demanda):
        demanda.arquivada = True
        db.commit()
    return RedirectResponse("/demandas", status_code=303)


@router.post("/demandas/{demanda_id}/restaurar")
def restaurar_demanda(demanda_id: int, usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    demanda = db.get(models.Demanda, demanda_id)
    if demanda and _pode_editar(usuario, demanda):
        demanda.arquivada = False
        db.commit()
    return RedirectResponse(f"/demandas/{demanda_id}", status_code=303)


@router.get("/demandas/{demanda_id}")
def detalhe_demanda(demanda_id: int, request: Request, usuario=Depends(exigir_login),
                     db: Session = Depends(get_db)):
    demanda = db.get(models.Demanda, demanda_id)
    return templates.TemplateResponse("demanda_detalhe.html", {
        "request": request, "usuario": usuario, "demanda": demanda,
    })
