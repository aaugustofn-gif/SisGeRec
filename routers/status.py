from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import datetime as dt
from database import get_db
from auth import exigir_login, exigir_perfil
from utils import proximo_status, eh_status_final, int_ou_none, construir_linha_tempo
import models
from webtemplates import templates

router = APIRouter()


def _ordenar(linhas):
    return sorted(linhas, key=lambda l: (l.ordem_manual, l.data_criacao))


@router.get("/status")
def painel_status(request: Request, status_filtro: str = None, tipo_processo: str = None,
                   nd: str = None, origem_id: str = None, setor: str = None,
                   usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    origem_id = int_ou_none(origem_id)
    q = db.query(models.LinhaStatus)
    linhas = q.all()

    def combina(linha):
        demanda = linha.autorizacao.demanda
        if status_filtro and linha.status_atual != status_filtro:
            return False
        if tipo_processo and linha.tipo_processo != tipo_processo:
            return False
        if nd and demanda.nd != nd:
            return False
        if origem_id and linha.autorizacao.origem_id != origem_id:
            return False
        if setor and demanda.setor != setor:
            return False
        return True

    linhas = [l for l in linhas if combina(l)]
    linhas = _ordenar(linhas)

    linhas_tempo = {
        l.id: construir_linha_tempo(db, l)
        for l in linhas if l.tipo_processo and not l.autorizacao.cancelada
    }
    ultimos_passos = {
        l.id: eh_status_final(db, l.tipo_processo, l.status_atual)
        for l in linhas if l.tipo_processo and not l.autorizacao.cancelada
    }

    origens = db.query(models.Origem).order_by(models.Origem.nome).all()

    return templates.TemplateResponse("status.html", {
        "request": request, "usuario": usuario, "linhas": linhas, "origens": origens,
        "nd_choices": models.ND_CHOICES, "setor_choices": models.SETOR_CHOICES,
        "tipo_processo_choices": models.TIPO_PROCESSO_CHOICES,
        "tipo_processo_labels": models.TIPO_PROCESSO_LABELS,
        "linhas_tempo": linhas_tempo, "ultimos_passos": ultimos_passos,
        "filtros": {"status": status_filtro, "tipo_processo": tipo_processo, "nd": nd,
                    "origem_id": origem_id, "setor": setor},
    })


def _responder(request, db, linha, usuario, ajax: str, abrir_observacoes: str = ""):
    """Se a ação veio via AJAX, devolve só o cartão re-renderizado (para substituição
    no lugar, sem recarregar a página). Caso contrário, faz o redirecionamento normal —
    isso mantém o sistema funcionando mesmo com JavaScript desabilitado."""
    if not ajax:
        return RedirectResponse("/status", status_code=303)

    db.refresh(linha)
    passos = None
    no_ultimo = False
    if linha.tipo_processo and not linha.autorizacao.cancelada:
        passos = construir_linha_tempo(db, linha)
        no_ultimo = eh_status_final(db, linha.tipo_processo, linha.status_atual)

    return templates.TemplateResponse("_status_card.html", {
        "request": request, "usuario": usuario, "l": linha,
        "passos": passos, "no_ultimo_passo": no_ultimo,
        "abrir_observacoes": bool(abrir_observacoes),
        "tipo_processo_choices": models.TIPO_PROCESSO_CHOICES,
        "tipo_processo_labels": models.TIPO_PROCESSO_LABELS,
    })


@router.post("/status/{linha_id}/definir-tipo")
def definir_tipo_processo(request: Request, linha_id: int, tipo_processo: str = Form(...),
                           ajax: str = Form(""), abrir_observacoes: str = Form(""),
                           usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    linha = db.get(models.LinhaStatus, linha_id)
    if linha and not linha.tipo_processo and not linha.autorizacao.cancelada:
        linha.tipo_processo = tipo_processo
        db.commit()
    if not linha:
        return RedirectResponse("/status", status_code=303)
    return _responder(request, db, linha, usuario, ajax, abrir_observacoes)


@router.post("/status/{linha_id}/avancar")
def avancar_status(request: Request, linha_id: int,
                    ajax: str = Form(""), abrir_observacoes: str = Form(""),
                    usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    linha = db.get(models.LinhaStatus, linha_id)
    if not linha:
        return RedirectResponse("/status", status_code=303)
    if not linha.tipo_processo or linha.autorizacao.cancelada:
        return _responder(request, db, linha, usuario, ajax, abrir_observacoes)

    demanda = linha.autorizacao.demanda
    if usuario.nip != demanda.militar_responsavel_nip and usuario.perfil not in ("ADMIN", "SUPERADMIN"):
        return _responder(request, db, linha, usuario, ajax, abrir_observacoes)

    novo = proximo_status(db, linha.tipo_processo, linha.status_atual)
    if novo is None:
        # Já está no último passo configurado — nada a avançar.
        return _responder(request, db, linha, usuario, ajax, abrir_observacoes)

    agora = dt.datetime.utcnow()
    linha.status_atual = novo
    db.add(models.StatusHistorico(
        linha_status_id=linha.id, status=novo, data=agora, alterado_por_nip=usuario.nip,
    ))

    if eh_status_final(db, linha.tipo_processo, novo):
        linha.ordem_manual = 1

    db.commit()
    return _responder(request, db, linha, usuario, ajax, abrir_observacoes)


@router.post("/status/{linha_id}/cancelar")
def cancelar_processo(request: Request, linha_id: int, motivo: str = Form(""),
                       ajax: str = Form(""), abrir_observacoes: str = Form(""),
                       usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    """Cancela a autorização/processo de aquisição: libera o saldo (deixa de ser debitado)
    e a demanda volta a ter saldo pendente de autorização, podendo ser autorizada novamente."""
    linha = db.get(models.LinhaStatus, linha_id)
    if not linha:
        return RedirectResponse("/status", status_code=303)
    if linha.autorizacao.cancelada:
        return _responder(request, db, linha, usuario, ajax, abrir_observacoes)

    agora = dt.datetime.utcnow()
    autorizacao = linha.autorizacao
    autorizacao.cancelada = True
    autorizacao.data_cancelamento = agora
    autorizacao.cancelado_por_nip = usuario.nip
    autorizacao.motivo_cancelamento = motivo or None

    linha.status_atual = "CANCELADA"
    linha.ordem_manual = 1
    db.add(models.StatusHistorico(
        linha_status_id=linha.id, status="CANCELADA", data=agora, alterado_por_nip=usuario.nip,
    ))
    db.commit()
    return _responder(request, db, linha, usuario, ajax, abrir_observacoes)


# ---- Observações do processo (acumuladas desde a aprovação pelo CEM) ----

@router.post("/status/{linha_id}/observacoes")
def adicionar_observacao(request: Request, linha_id: int, texto: str = Form(...),
                          ajax: str = Form(""), abrir_observacoes: str = Form(""),
                          usuario=Depends(exigir_login), db: Session = Depends(get_db)):
    linha = db.get(models.LinhaStatus, linha_id)
    if not linha:
        return RedirectResponse("/status", status_code=303)

    demanda = linha.autorizacao.demanda
    pode = (usuario.nip == demanda.militar_responsavel_nip
            or usuario.perfil in ("ADMIN", "SUPERADMIN", "CEM"))
    if pode and texto.strip():
        db.add(models.ObservacaoProcesso(
            linha_status_id=linha.id, texto=texto.strip(), autor_nip=usuario.nip,
        ))
        db.commit()
    return _responder(request, db, linha, usuario, ajax, abrir_observacoes)


# ---- Configuração de listas de status por tipo de processo (ADMIN) ----

@router.get("/status/config")
def config_status(request: Request, usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    listas = {}
    for tipo in models.TIPO_PROCESSO_CHOICES:
        itens = (
            db.query(models.StatusConfig)
            .filter(models.StatusConfig.tipo_processo == tipo)
            .order_by(models.StatusConfig.ordem)
            .all()
        )
        listas[tipo] = itens
    return templates.TemplateResponse("admin_status_config.html", {
        "request": request, "usuario": usuario, "listas": listas,
        "tipo_processo_choices": models.TIPO_PROCESSO_CHOICES,
        "tipo_processo_labels": models.TIPO_PROCESSO_LABELS,
        "setor_choices": models.SETOR_CHOICES,
    })


def _int_ou_none_form(valor: str):
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        n = int(valor)
        return n if n > 0 else None
    except ValueError:
        return None


@router.post("/status/config/{tipo_processo}/adicionar")
def adicionar_status_config(tipo_processo: str, nome_status: str = Form(...), setor: str = Form(""),
                             prazo: str = Form(""),
                             usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    maior_ordem = (
        db.query(models.StatusConfig)
        .filter(models.StatusConfig.tipo_processo == tipo_processo)
        .count()
    )
    if nome_status.strip():
        db.add(models.StatusConfig(tipo_processo=tipo_processo, ordem=maior_ordem + 1,
                                    nome_status=nome_status.strip(), setor=setor or None,
                                    prazo=_int_ou_none_form(prazo)))
        db.commit()
    return RedirectResponse("/status/config", status_code=303)


@router.post("/status/config/{item_id}/editar")
def editar_status_config(item_id: int, nome_status: str = Form(...), setor: str = Form(""),
                          prazo: str = Form(""),
                          usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    item = db.get(models.StatusConfig, item_id)
    if not item or not nome_status.strip():
        return RedirectResponse("/status/config", status_code=303)

    nome_antigo = item.nome_status
    item.nome_status = nome_status.strip()
    item.setor = setor or None
    item.prazo = _int_ou_none_form(prazo)

    if nome_antigo != item.nome_status:
        linhas_no_passo = (
            db.query(models.LinhaStatus)
            .filter(models.LinhaStatus.tipo_processo == item.tipo_processo,
                    models.LinhaStatus.status_atual == nome_antigo)
            .all()
        )
        for linha in linhas_no_passo:
            linha.status_atual = item.nome_status

        historicos_no_passo = (
            db.query(models.StatusHistorico)
            .join(models.LinhaStatus, models.StatusHistorico.linha_status_id == models.LinhaStatus.id)
            .filter(models.LinhaStatus.tipo_processo == item.tipo_processo,
                    models.StatusHistorico.status == nome_antigo)
            .all()
        )
        for h in historicos_no_passo:
            h.status = item.nome_status

    db.commit()
    return RedirectResponse("/status/config", status_code=303)


@router.post("/status/config/{item_id}/remover")
def remover_status_config(item_id: int, usuario=Depends(exigir_perfil("ADMIN")), db: Session = Depends(get_db)):
    item = db.get(models.StatusConfig, item_id)
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse("/status/config", status_code=303)
