from sqlalchemy.orm import Session
from sqlalchemy import func
from decimal import Decimal
import io
import datetime as dt
from openpyxl import Workbook
import models


def calcular_saldos(db: Session):
    """Retorna dict {(nd, origem_id): saldo_decimal} considerando lançamentos - autorizações
    ativas (não canceladas), usando o valor unitário efetivo de cada autorização."""
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


def int_ou_none(valor):
    """Converte string de query param em int, tratando '' (opção 'Todas' dos filtros) como None."""
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def lista_status_tipo_processo(db: Session, tipo_processo: str):
    itens = (
        db.query(models.StatusConfig)
        .filter(models.StatusConfig.tipo_processo == tipo_processo)
        .order_by(models.StatusConfig.ordem)
        .all()
    )
    return [i.nome_status for i in itens]


def proximo_status(db: Session, tipo_processo: str, status_atual: str):
    lista = lista_status_tipo_processo(db, tipo_processo)
    if not lista:
        return None
    if status_atual not in lista:
        return lista[0]
    idx = lista.index(status_atual)
    if idx + 1 < len(lista):
        return lista[idx + 1]
    return None


def eh_status_final(db: Session, tipo_processo: str, status_atual: str) -> bool:
    lista = lista_status_tipo_processo(db, tipo_processo)
    return bool(lista) and status_atual == lista[-1]


def processo_em_andamento(db: Session, linha) -> bool:
    """Verdadeiro para qualquer autorização ativa que ainda não foi marcada como concluída."""
    if linha.autorizacao.cancelada or linha.concluido:
        return False
    return True


def esta_atrasado(db: Session, linha) -> bool:
    """Verdadeiro se a linha está parada no passo atual há mais dias do que o prazo
    configurado para esse passo (dentro do seu tipo de processo). Um processo já
    concluído nunca é considerado atrasado."""
    if linha.autorizacao.cancelada or linha.concluido or not linha.tipo_processo:
        return False
    config = (
        db.query(models.StatusConfig)
        .filter(models.StatusConfig.tipo_processo == linha.tipo_processo,
                models.StatusConfig.nome_status == linha.status_atual)
        .first()
    )
    if not config or not config.prazo:
        return False
    data_entrada = None
    for h in linha.historico:
        if h.status == linha.status_atual:
            data_entrada = h.data
    if not data_entrada:
        return False
    dias_no_passo = (dt.datetime.utcnow() - data_entrada).days
    return dias_no_passo > config.prazo


def _passou_do_passo(idx_atual: int, idx_passo, concluido: bool) -> bool:
    """Um passo só é considerado 'passado por completo' quando a linha já avançou para um
    passo posterior, ou quando esse era o último passo e o processo foi marcado como
    concluído (clique extra de 'Avançar status' no último passo)."""
    if idx_passo is None:
        return False
    if idx_atual > idx_passo:
        return True
    if idx_atual == idx_passo and concluido:
        return True
    return False


def bucket_financeiro(db: Session, linha) -> str:
    """Classifica em qual estágio financeiro a linha está: 'em_processo' (ainda não passou
    por 'Empenhado'), 'empenhado' (já passou por 'Empenhado' mas não por 'Liquidado') ou
    'liquidado' (já passou por 'Liquidado'). Estar PARADO em Empenhado/Liquidado (ainda em
    amarelo, sem ter avançado ou concluído) não conta como tendo alcançado esse estágio."""
    if not linha or not linha.tipo_processo:
        return "em_processo"

    lista = lista_status_tipo_processo(db, linha.tipo_processo)
    idx_empenhado = lista.index("Empenhado") if "Empenhado" in lista else None
    idx_liquidado = lista.index("Liquidado") if "Liquidado" in lista else None
    idx_atual = lista.index(linha.status_atual) if linha.status_atual in lista else -1

    if _passou_do_passo(idx_atual, idx_liquidado, linha.concluido):
        return "liquidado"
    if _passou_do_passo(idx_atual, idx_empenhado, linha.concluido):
        return "empenhado"
    return "em_processo"


def resumo_financeiro_por_nd(db: Session):
    """Monta, para cada ND, o total Disponível (Recursos), Em processo, Empenhado e Liquidado."""
    saldos = calcular_saldos(db)
    disponivel = {}
    for (nd, _origem_id), valor in saldos.items():
        disponivel[nd] = disponivel.get(nd, Decimal("0")) + valor

    em_processo = {n: Decimal("0") for n in models.ND_CHOICES}
    empenhado = {n: Decimal("0") for n in models.ND_CHOICES}
    liquidado = {n: Decimal("0") for n in models.ND_CHOICES}

    autorizacoes = db.query(models.Autorizacao).filter(models.Autorizacao.cancelada == False).all()
    for a in autorizacoes:
        nd = a.demanda.nd
        valor = Decimal(a.quantidade_autorizada) * Decimal(a.valor_unitario_efetivo())
        bucket = bucket_financeiro(db, a.linha_status)
        if bucket == "liquidado":
            liquidado[nd] = liquidado.get(nd, Decimal("0")) + valor
        elif bucket == "empenhado":
            empenhado[nd] = empenhado.get(nd, Decimal("0")) + valor
        else:
            em_processo[nd] = em_processo.get(nd, Decimal("0")) + valor

    return [
        {
            "nd": nd,
            "disponivel": disponivel.get(nd, Decimal("0")),
            "em_processo": em_processo.get(nd, Decimal("0")),
            "empenhado": empenhado.get(nd, Decimal("0")),
            "liquidado": liquidado.get(nd, Decimal("0")),
        }
        for nd in models.ND_CHOICES
    ]


def construir_linha_tempo(db: Session, linha):
    """Monta a lista de TODOS os passos do tipo de processo da linha (incluindo o estado inicial
    'AUTORIZADA'), marcando qual é o atual (e se está atrasado) e a data em que cada passo já
    concluído foi alcançado."""
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
    atrasado = esta_atrasado(db, linha)

    resultado = []
    for i, p in enumerate(passos):
        if linha.concluido:
            concluido_passo = i <= idx_atual
            atual_passo = False
            atrasado_passo = False
        else:
            concluido_passo = i < idx_atual
            atual_passo = i == idx_atual
            atrasado_passo = atual_passo and atrasado
        resultado.append({
            "nome": p["nome"],
            "setor": p["setor"],
            "atual": atual_passo,
            "atrasado": atrasado_passo,
            "concluido": concluido_passo,
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
