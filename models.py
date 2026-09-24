import datetime as dt
from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from database import Base

ND_CHOICES = ["15", "30", "33", "39", "52"]
SETOR_CHOICES = ["G10", "G20", "G30", "G40", "SECOM", "C Msg", "ComSoc", "Info"]
PERFIL_CHOICES = ["SUPERADMIN", "ADMIN", "CEM", "COMUM"]
TIPO_PROCESSO_CHOICES = ["LICITADO", "ADESAO", "DISPENSA", "CSF", "TRANSF_OM", "DIARIAS"]
TIPO_PROCESSO_LABELS = {
    "LICITADO": "Já licitado",
    "ADESAO": "Adesão",
    "DISPENSA": "Dispensa de licitação",
    "CSF": "Cartão de suprimento de fundos",
    "TRANSF_OM": "Transferência de crédito para outra OM",
    "DIARIAS": "Pagamento de diárias",
}
STATUS_INICIAL = "AUTORIZADA"


class Usuario(Base):
    __tablename__ = "usuarios"
    nip = Column(String(20), primary_key=True)  # ID do usuário = NIP
    posto = Column(String(50), nullable=False)
    nome = Column(String(150), nullable=False)
    setor = Column(String(20), nullable=False)
    perfil = Column(String(20), nullable=False)
    senha_hash = Column(String(255), nullable=False)
    deve_trocar_senha = Column(Boolean, default=True, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    data_cadastro = Column(DateTime, default=dt.datetime.utcnow)

    def nome_completo(self):
        return f"{self.posto} {self.nome}"


class Origem(Base):
    __tablename__ = "origens"
    id = Column(Integer, primary_key=True)
    nome = Column(String(150), nullable=False, unique=True)
    ativo = Column(Boolean, default=True, nullable=False)


class Recurso(Base):
    __tablename__ = "recursos"
    id = Column(Integer, primary_key=True)
    nd = Column(String(2), nullable=False)
    origem_id = Column(Integer, ForeignKey("origens.id"), nullable=False)
    valor = Column(Numeric(14, 2), nullable=False)
    observacoes = Column(Text, nullable=True)
    cadastrado_por_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=False)
    data_cadastro = Column(DateTime, default=dt.datetime.utcnow)

    origem = relationship("Origem")
    cadastrado_por = relationship("Usuario")


class Demanda(Base):
    __tablename__ = "demandas"
    id = Column(Integer, primary_key=True)
    descricao = Column(String(300), nullable=False)
    quantidade = Column(Integer, nullable=False)
    nd = Column(String(2), nullable=False)
    valor_unitario = Column(Numeric(14, 2), nullable=False)
    origem_desejada_id = Column(Integer, ForeignKey("origens.id"), nullable=True)
    militar_responsavel_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=False)
    setor = Column(String(20), nullable=False)
    observacoes = Column(Text, nullable=True)
    data_cadastro = Column(DateTime, default=dt.datetime.utcnow)
    arquivada = Column(Boolean, default=False, nullable=False)

    origem_desejada = relationship("Origem")
    militar_responsavel = relationship("Usuario")
    autorizacoes = relationship("Autorizacao", back_populates="demanda")

    def valor_total(self):
        return float(self.quantidade) * float(self.valor_unitario)

    def autorizacoes_ativas(self):
        return [a for a in self.autorizacoes if not a.cancelada]

    def quantidade_autorizada_acumulada(self):
        return sum(a.quantidade_autorizada for a in self.autorizacoes_ativas())

    def quantidade_pendente(self):
        return self.quantidade - self.quantidade_autorizada_acumulada()

    def status_geral(self):
        pend = self.quantidade_pendente()
        if pend <= 0:
            return "Totalmente autorizada"
        if self.quantidade_autorizada_acumulada() > 0:
            return "Parcialmente autorizada"
        return "Pendente"


class Autorizacao(Base):
    __tablename__ = "autorizacoes"
    id = Column(Integer, primary_key=True)
    demanda_id = Column(Integer, ForeignKey("demandas.id"), nullable=False)
    quantidade_autorizada = Column(Integer, nullable=False)
    origem_id = Column(Integer, ForeignKey("origens.id"), nullable=False)
    valor_unitario = Column(Numeric(14, 2), nullable=True)  # sincronizado com a demanda (ver rota de edição)
    data_ratificacao = Column(DateTime, default=dt.datetime.utcnow)
    ratificado_por_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=False)

    cancelada = Column(Boolean, default=False, nullable=False)
    data_cancelamento = Column(DateTime, nullable=True)
    cancelado_por_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=True)
    motivo_cancelamento = Column(Text, nullable=True)

    demanda = relationship("Demanda", back_populates="autorizacoes")
    origem = relationship("Origem")
    ratificado_por = relationship("Usuario", foreign_keys=[ratificado_por_nip])
    cancelado_por = relationship("Usuario", foreign_keys=[cancelado_por_nip])
    linha_status = relationship("LinhaStatus", back_populates="autorizacao", uselist=False)

    def valor_unitario_efetivo(self):
        return self.valor_unitario if self.valor_unitario is not None else self.demanda.valor_unitario

    def valor(self):
        return float(self.quantidade_autorizada) * float(self.valor_unitario_efetivo())


class LinhaStatus(Base):
    __tablename__ = "linhas_status"
    id = Column(Integer, primary_key=True)
    autorizacao_id = Column(Integer, ForeignKey("autorizacoes.id"), nullable=False, unique=True)
    tipo_processo = Column(String(20), nullable=True)
    status_atual = Column(String(80), nullable=False, default=STATUS_INICIAL)
    ordem_manual = Column(Integer, default=0)  # usado para empurrar ao fim quando concluída/cancelada
    data_criacao = Column(DateTime, default=dt.datetime.utcnow)
    concluido = Column(Boolean, default=False, nullable=False)
    data_conclusao = Column(DateTime, nullable=True)

    autorizacao = relationship("Autorizacao", back_populates="linha_status")
    historico = relationship("StatusHistorico", back_populates="linha_status",
                              order_by="StatusHistorico.data")
    observacoes = relationship("ObservacaoProcesso", back_populates="linha_status",
                                order_by="ObservacaoProcesso.data")


class StatusHistorico(Base):
    __tablename__ = "status_historico"
    id = Column(Integer, primary_key=True)
    linha_status_id = Column(Integer, ForeignKey("linhas_status.id"), nullable=False)
    status = Column(String(80), nullable=False)
    data = Column(DateTime, default=dt.datetime.utcnow)
    alterado_por_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=False)

    linha_status = relationship("LinhaStatus", back_populates="historico")
    alterado_por = relationship("Usuario")


class ObservacaoProcesso(Base):
    """Anotações livres acumuladas ao longo do processo de aquisição de uma parcela autorizada."""
    __tablename__ = "observacoes_processo"
    id = Column(Integer, primary_key=True)
    linha_status_id = Column(Integer, ForeignKey("linhas_status.id"), nullable=False)
    texto = Column(Text, nullable=False)
    data = Column(DateTime, default=dt.datetime.utcnow)
    autor_nip = Column(String(20), ForeignKey("usuarios.nip"), nullable=False)

    linha_status = relationship("LinhaStatus", back_populates="observacoes")
    autor = relationship("Usuario")


class StatusConfig(Base):
    """Lista de status configurável por tipo de processo, na ordem em que devem ocorrer."""
    __tablename__ = "status_config"
    id = Column(Integer, primary_key=True)
    tipo_processo = Column(String(20), nullable=False)
    ordem = Column(Integer, nullable=False)
    nome_status = Column(String(80), nullable=False)
    setor = Column(String(100), nullable=True)  # setor/responsável por executar este passo (texto livre)
    prazo = Column(Integer, nullable=True)  # prazo máximo em dias para este passo
