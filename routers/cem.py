from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from decimal import Decimal
from typing import List
import datetime as dt
from database import get_db
from auth import exigir_perfil
from utils import calcular_saldos
import models
from webtemplates import templates

router = APIRouter()


@router.get("/cem/autorizar")
def tela_autorizacao(request: Request, nd: List[str] = Query(default=[]),
                      usuario=Depends(exigir_perfil("CEM")), db: Session = Depends(get_db)):
    nds_selecionadas = [n for n in nd if n in models.ND_CHOICES]

    demandas = db.query(models.Demanda).order_by(models.Demanda.data_cadastro.asc()).all()
    demandas_pendentes = [d for d in demandas if d.quantidade_pendente() > 0 and not d.arquivada]
    if nds_selecionadas:
        demandas_pendentes = [d for d in demandas_pendentes if d.nd in nds_selecionadas]

    origens = db.query(models.Origem).filter(models.Origem.ativo == True).order_by(models.Origem.nome).all()
    saldos = calcular_saldos(db)

    nds_para_saldo = nds_selecionadas if nds_selecionadas else models.ND_CHOICES
    saldos_serializaveis = {
        f"{n}|{oid}": float(v) for (n, oid), v in saldos.items() if n in nds_para_saldo
    }

    return templates.TemplateResponse("cem_autorizacao.html", {
        "request": request, "usuario": usuario, "demandas": demandas_pendentes,
        "origens": origens, "saldos": saldos, "saldos_json": saldos_serializaveis,
        "nd_choices": models.ND_CHOICES, "nds_selecionadas": nds_selecionadas,
        "nds_para_saldo": nds_para_saldo,
    })


@router.post("/cem/ratificar")
async def ratificar(request: Request, nd: List[str] = Query(default=[]),
                     usuario=Depends(exigir_perfil("CEM")), db: Session = Depends(get_db)):
    form = await request.form()

    autorizacoes_a_criar = []
    saldos = calcular_saldos(db)

    for chave, valor in form.items():
        if chave.startswith("qtd_") and valor.strip():
            demanda_id = int(chave.replace("qtd_", ""))
            try:
                qtd = int(valor)
            except ValueError:
                continue
            if qtd <= 0:
                continue
            origem_id_raw = form.get(f"origem_{demanda_id}")
            if not origem_id_raw:
                continue
            origem_id = int(origem_id_raw)

            demanda = db.get(models.Demanda, demanda_id)
            if not demanda:
                continue
            pendente = demanda.quantidade_pendente()
            if qtd > pendente:
                qtd = pendente
            if qtd <= 0:
                continue

            debito = Decimal(qtd) * Decimal(demanda.valor_unitario)
            chave_saldo = (demanda.nd, origem_id)
            saldo_atual = saldos.get(chave_saldo, Decimal("0"))
            if debito > saldo_atual:
                continue

            saldos[chave_saldo] = saldo_atual - debito
            autorizacoes_a_criar.append((demanda, qtd, origem_id))

    agora = dt.datetime.utcnow()
    for demanda, qtd, origem_id in autorizacoes_a_criar:
        autorizacao = models.Autorizacao(
            demanda_id=demanda.id, quantidade_autorizada=qtd, origem_id=origem_id,
            valor_unitario=demanda.valor_unitario,
            data_ratificacao=agora, ratificado_por_nip=usuario.nip,
        )
        db.add(autorizacao)
        db.flush()

        linha = models.LinhaStatus(autorizacao_id=autorizacao.id, status_atual=models.STATUS_INICIAL)
        db.add(linha)
        db.flush()

        db.add(models.StatusHistorico(
            linha_status_id=linha.id, status=models.STATUS_INICIAL,
            data=agora, alterado_por_nip=usuario.nip,
        ))

    db.commit()

    destino = "/cem/autorizar"
    if nd:
        destino += "?" + "&".join(f"nd={n}" for n in nd)
    return RedirectResponse(destino, status_code=303)
