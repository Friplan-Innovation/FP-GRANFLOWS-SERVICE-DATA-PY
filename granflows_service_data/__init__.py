"""Cliente Python da GranFlows Data API.

Uma implementação, versionada por tag, que os serviços instalam — em vez de uma
cópia por serviço, que divergiria e cuja divergência só apareceria no serviço
que ficasse para trás.

    from granflows_service_data import ClienteDataApi, RecursoNaoEncontrado

    cliente = ClienteDataApi(cfg.data_api_url)          # no boot
    sessao = cliente.para(identidade.data_api_token)    # por requisição
    itens = sessao.get("/v1/exemplo/itens")

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

__version__ = "0.1.1"
