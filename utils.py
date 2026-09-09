from sqlalchemy.orm import Session
from sqlalchemy import func
from decimal import Decimal
import io
from openpyxl import Workbook
import models


def calcular_saldos(db: Session):
    """Retorna dict {(nd, origem_id): saldo_decimal} considerando lançamentos - autorizações ratificadas."""
    saldos = {}
    for r in db.query(models.Recurso).all():
        chave = (r.nd, r.origem_id)
        saldos[chave] = saldos.get(chave, Decimal("0")) + Decimal(r.valor)

    for a in db.query(models.Autorizacao).filter(models.Autorizacao.cancelada == False).all():
        demanda = a.demanda
        chave = (demanda.nd, a.origem_id)
        debito = Decimal(a.quantidade_autorizada) * Decimal(a.valor_unitario_efetivo())
        saldos[chave] = saldos.get(chave, Decimal("0")) - debito

    return saldos


def saldo_nd_origem(db: Session, nd: str, origem_id: int) -> Decimal:
    saldos = calcular_saldos(db)
    return saldos.get((nd, origem_id), Decimal("0"))


def lista_status_tipo_processo(db: Session, tipo_processo: str):
    itens = (
        db.query(models.StatusConfig)
        .filter(models.StatusConfig.tipo_processo == tipo_processo)
        .order_by(models.StatusConfig.ordem)
        .all()
    )
    return [i.nome_status for i in itens]


def proximo_status(db: Session, tipo_processo: str, status_atual: str):
    """Retorna o próximo status da lista configurada, ou None se já está no último (ou lista vazia)."""
    lista = lista_status_tipo_processo(db, tipo_processo)
    if not lista:
        return None
    if status_atual not in lista:
        return lista[0]
    idx = lista.index(status_atual)
    if idx + 1 < len(lista):
        return lista[idx + 1]
    return None  # já está no último status


def int_ou_none(valor):
    """Converte string de query param em int, tratando '' (opção 'Todas' dos filtros) como None."""
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def eh_status_final(db: Session, tipo_processo: str, status_atual: str) -> bool:
    lista = lista_status_tipo_processo(db, tipo_processo)
    return bool(lista) and status_atual == lista[-1]


def construir_linha_tempo(db: Session, linha):
    """Monta a lista de TODOS os passos do tipo de processo da linha (incluindo o estado inicial
    'AUTORIZADA'), marcando qual é o atual e a data em que cada passo já concluído foi alcançado."""
    configs = (
        db.query(models.StatusConfig)
        .filter(models.StatusConfig.tipo_processo == linha.tipo_processo)
        .order_by(models.StatusConfig.ordem)
        .all()
    )
    passos = [{"nome": models.STATUS_INICIAL, "setor": None}]
    passos += [{"nome": c.nome_status, "setor": c.setor} for c in configs]

    datas = {}
    for h in linha.historico:
        if h.status not in datas:
            datas[h.status] = h.data

    nomes = [p["nome"] for p in passos]
    idx_atual = nomes.index(linha.status_atual) if linha.status_atual in nomes else 0

    resultado = []
    for i, p in enumerate(passos):
        resultado.append({
            "nome": p["nome"],
            "setor": p["setor"],
            "atual": i == idx_atual,
            "concluido": i < idx_atual,
            "data": datas.get(p["nome"]),
        })
    return resultado


def gerar_xlsx(headers, linhas, nome_aba="Planilha"):
    """Gera um arquivo .xlsx em memória a partir de cabeçalhos e linhas de dados."""
    wb = Workbook()
    ws = wb.active
    ws.title = nome_aba[:31]
    ws.append(headers)
    for linha in linhas:
        ws.append(linha)

    for col in ws.columns:
        largura = max((len(str(c.value)) if c.value is not None else 0) for c in col) + 2
        ws.column_dimensions[col[0].column_letter].width = min(largura, 50)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
