"""Cliente Python da Data API dos microserviços GranFlows.

Uma implementação, versionada por tag, que os seis serviços instalam — em vez
de seis cópias divergindo entre si, que é o que já aconteceu com o módulo
`platform/` (81 linhas de diferença entre duas gerações do mesmo arquivo).

    from granflows_service_data import ClienteDataApi, RecursoNaoEncontrado

    cliente = ClienteDataApi("https://ca-granflows-dev-dataapi.interno")   # no boot
    sessao = cliente.para(identidade.scope_token)                          # por requisição
    jobs = sessao.get("/v1/book/jobs")

Ver README.md para o contrato de erros e a regra de falha fechada.
"""

from .cliente import (
    TIMEOUT_PADRAO_SEGUNDOS,
    TIMEOUT_UPLOAD_SEGUNDOS,
    ClienteDataApi,
    SessaoDataApi,
)
from .erros import (
    Conflito,
    DataApiIndisponivel,
    EntradaInvalida,
    ErroDaDataApi,
    NaoAutorizado,
    RecursoNaoEncontrado,
)

__all__ = [
    "ClienteDataApi",
    "SessaoDataApi",
    "TIMEOUT_PADRAO_SEGUNDOS",
    "TIMEOUT_UPLOAD_SEGUNDOS",
    "ErroDaDataApi",
    "DataApiIndisponivel",
    "NaoAutorizado",
    "RecursoNaoEncontrado",
    "Conflito",
    "EntradaInvalida",
]

__version__ = "0.1.0"
