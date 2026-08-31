"""
O cliente HTTP da Data API dos serviços.

**Por que este pacote existe.** Os seis microserviços têm o mesmo módulo
`platform/` copiado entre si — e ele já divergiu: 81 linhas de diferença entre
o `session_store.py` do Book de Planejamento e o do Data Book, contra 12 entre
Data Book e Mapa de Juntas. São duas gerações do mesmo arquivo convivendo, e o
piloto está na antiga.

Escrever o cliente da Data API seis vezes produziria seis gerações do mesmo
defeito. Este pacote é a alternativa: uma implementação, versionada por tag, que
os seis instalam.

**Duas camadas, de propósito.** `ClienteDataApi` guarda o que é do PROCESSO — a
URL base, o timeout, o pool de conexões. `SessaoDataApi` guarda o que é da
REQUISIÇÃO — o scopeToken daquela pessoa. Não dá para chamar uma rota sem
passar por `cliente.para(token)`, porque os verbos só existem na sessão. É a
mesma ideia de `withScope()` do lado da API: a porta é única e o escopo é
obrigatório para atravessá-la.

**A URL base vem de configuração, nunca de requisição.** É o que fecha SSRF, e é
a mesma regra que `platform/config.py` já aplica ao `platform_api_url`. O
construtor valida o formato e recusa o que não for `scheme://host[:porta][/base]`.

**Falha fechada.** Timeout, erro de rede e 5xx viram `DataApiIndisponivel`, que
o serviço traduz em 503. Não existe caminho neste arquivo que devolva lista
vazia, `None` ou valor default quando a API não respondeu — degradar em silêncio
é como um histórico some sem ninguém perceber.

**O token nunca é logado.** Não aparece em mensagem de exceção, em repr, nem em
log de debug. As mensagens de erro daqui carregam método, caminho e status; o
`Authorization` fica de fora por construção, porque nunca é interpolado em
string nenhuma.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any, BinaryIO
from urllib.parse import urlparse

import httpx

from .erros import (
    Conflito,
    DataApiIndisponivel,
    EntradaInvalida,
    NaoAutorizado,
    RecursoNaoEncontrado,
)

# Curto de propósito. A Data API é vizinha dentro do mesmo Container Apps
# Environment; se ela demora mais que isto, está degradada, e segurar a thread
# do Gunicorn esperando piora a degradação em vez de contorná-la. Mesmo
# raciocínio do `socket_timeout` de `platform/session_store.py`.
TIMEOUT_PADRAO_SEGUNDOS = 10.0

# Upload de artefato é o único caminho que move megabytes e legitimamente leva
# mais tempo. Separado do timeout geral para que aumentar um não afrouxe o
# outro.
TIMEOUT_UPLOAD_SEGUNDOS = 120.0


class ClienteDataApi:
    """O que é do processo: URL base, timeouts e o pool de conexões.

    Instancie UMA vez no boot e reaproveite. Cada instância mantém um pool
    `httpx`; criar uma por requisição joga fora a reutilização de conexão e o
    handshake TLS junto.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = TIMEOUT_PADRAO_SEGUNDOS,
        timeout_upload_seconds: float = TIMEOUT_UPLOAD_SEGUNDOS,
        permitir_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = _url_de_api(base_url, permitir_http=permitir_http)
        self._timeout = timeout_seconds
        self._timeout_upload = timeout_upload_seconds
        # `transport` existe para o teste injetar um `MockTransport` sem subir
        # servidor. Nenhum caminho de produção passa esse argumento.
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
            transport=transport,
            # Sem `follow_redirects`: a Data API não redireciona, e seguir um
            # redirect cegamente com `Authorization` no cabeçalho mandaria o
            # token para onde quer que o Location apontasse.
            follow_redirects=False,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def para(self, scope_token: str) -> SessaoDataApi:
        """A sessão de UMA pessoa. O token vem da sessão server-side do serviço.

        Nunca de parâmetro de rota, corpo ou cabeçalho recebido do navegador —
        o token é emitido pelo plano de controle no exchange e guardado no
        Redis do próprio serviço.
        """
        if not scope_token or not scope_token.strip():
            # Falha aqui e não na primeira chamada: um token vazio produziria
            # 401 da API, que o serviço traduziria em "sua sessão venceu" para
            # uma pessoa cuja sessão está perfeitamente viva.
            raise ValueError("scope_token vazio")
        return SessaoDataApi(self, scope_token)

    def fechar(self) -> None:
        self._http.close()

    def __enter__(self) -> ClienteDataApi:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.fechar()

    def __repr__(self) -> str:
        # Sem token porque não há token aqui, e sem segredo nenhum. Explícito
        # para que ninguém acrescente um depois sem reparar.
        return f"ClienteDataApi(base_url={self._base_url!r})"


class SessaoDataApi:
    """O que é da requisição: o scopeToken de quem está usando o sistema.

    Obtida por `ClienteDataApi.para(token)`. Os verbos só existem aqui, então
    não há caminho que chame a Data API sem escopo.
    """

    def __init__(self, cliente: ClienteDataApi, scope_token: str) -> None:
        self._cliente = cliente
        self._token = scope_token

    # ── verbos ───────────────────────────────────────────────────────────

    def get(self, caminho: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self._chamar("GET", caminho, params=params)

    def post(self, caminho: str, corpo: Mapping[str, Any] | None = None) -> Any:
        return self._chamar("POST", caminho, json=corpo)

    def patch(self, caminho: str, corpo: Mapping[str, Any]) -> Any:
        return self._chamar("PATCH", caminho, json=corpo)

    def enviar_arquivo(
        self,
        caminho: str,
        *,
        campo: str,
        nome: str,
        conteudo: BinaryIO,
        tipo: str,
        campos: Mapping[str, str] | None = None,
    ) -> Any:
        """`multipart/form-data` — o caminho dos artefatos.

        Timeout próprio, mais folgado: é o único caminho que move megabytes.
        """
        return self._chamar(
            "POST",
            caminho,
            files={campo: (nome, conteudo, tipo)},
            data=dict(campos or {}),
            timeout=self._cliente._timeout_upload,
        )

    def baixar(self, caminho: str) -> bytes:
        """Conteúdo binário de um artefato.

        Devolve bytes, não JSON. Mesmo tratamento de erro dos demais verbos.
        """
        resposta = self._executar("GET", caminho, timeout=self._cliente._timeout_upload)
        self._levantar_se_erro(resposta, "GET", caminho)
        return resposta.content

    # ── o motor ──────────────────────────────────────────────────────────

    def _chamar(self, metodo: str, caminho: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", None)
        resposta = self._executar(metodo, caminho, timeout=timeout, **kwargs)
        self._levantar_se_erro(resposta, metodo, caminho)

        if resposta.status_code == 204 or not resposta.content:
            return None
        try:
            return resposta.json()
        except ValueError as e:
            # 2xx com corpo que não é JSON é contrato quebrado, não
            # indisponibilidade. Mas a única coisa que o serviço pode fazer é
            # a mesma: falhar fechado.
            raise DataApiIndisponivel(
                f"{metodo} {caminho}: resposta 2xx não é JSON"
            ) from e

    def _executar(self, metodo: str, caminho: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout", None) or self._cliente._timeout
        try:
            return self._cliente._http.request(
                metodo,
                caminho,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout,
                **kwargs,
            )
        except httpx.TimeoutException as e:
            # SEM RETRY, e é decisão. Uma escrita retentada às cegas é uma
            # emissão duplicada esperando o `UNIQUE` do banco recusar — e o
            # timeout não diz se a primeira chegou. Para leitura o retry seria
            # seguro, mas esconder latência atrás de tentativas silenciosas faz
            # a degradação aparecer como lentidão em vez de como erro, e aí
            # ninguém investiga.
            raise DataApiIndisponivel(f"{metodo} {caminho}: timeout") from e
        except httpx.HTTPError as e:
            # `type(e).__name__` e não `str(e)`: a mensagem do httpx pode
            # carregar a URL completa, e a URL de algumas rotas tem
            # identificador de recurso dentro.
            raise DataApiIndisponivel(
                f"{metodo} {caminho}: erro de rede ({type(e).__name__})"
            ) from e

    @staticmethod
    def _levantar_se_erro(resposta: httpx.Response, metodo: str, caminho: str) -> None:
        codigo = resposta.status_code
        if codigo < 300:
            return

        onde = f"{metodo} {caminho}"
        if codigo < 400:
            # 3xx. O cliente não segue redirect (`follow_redirects=False`), e
            # sem esta cláusula o 302 escapava pela guarda de `< 400`, caía no
            # `not resposta.content` de `_chamar` e virava um `None` — uma
            # resposta vazia indistinguível de sucesso. Foi o teste
            # `test_nao_segue_redirect` que expôs isso.
            #
            # A Data API não redireciona: um 3xx significa proxy no caminho ou
            # ingress mal configurado, e as duas coisas são indisponibilidade.
            raise DataApiIndisponivel(f"{onde}: respondeu {codigo} — redirect não é seguido")
        if codigo in (401, 403):
            raise NaoAutorizado(f"{onde}: scopeToken recusado ({codigo})")
        if codigo == 404:
            raise RecursoNaoEncontrado(f"{onde}: não encontrado")
        if codigo == 409:
            # A frase da API entra aqui porque o serviço às vezes a repassa —
            # "emissão já registrada" é literal do contrato e o Book a
            # reconhece. Erro 409 não carrega dado de outro dono.
            raise Conflito(f"{onde}: {_detalhe(resposta) or 'conflito'}")
        if codigo in (400, 422):
            raise EntradaInvalida(f"{onde}: {_detalhe(resposta) or 'entrada recusada'}")
        # 5xx e qualquer outro 4xx inesperado. O serviço não tem o que fazer
        # com a diferença, e os dois significam a mesma coisa para ele: não deu
        # para gravar nem ler, então não finja que deu.
        raise DataApiIndisponivel(f"{onde}: respondeu {codigo}")

    def __repr__(self) -> str:
        # NUNCA o token. Este método existe para garantir isso: sem ele, o
        # `__repr__` default do Python exporia os atributos numa stack trace.
        return f"SessaoDataApi(base_url={self._cliente.base_url!r})"


def _detalhe(resposta: httpx.Response) -> str:
    """A mensagem que a API mandou, quando ela mandou uma.

    Nunca levanta: já estamos no caminho de erro, e falhar ao formatar um erro
    substituiria o diagnóstico real por um `ValueError` de JSON.
    """
    try:
        corpo = resposta.json()
    except ValueError:
        return ""
    if isinstance(corpo, dict):
        mensagem = corpo.get("message") or corpo.get("erro")
        if isinstance(mensagem, str):
            return mensagem
        if isinstance(mensagem, list) and mensagem and isinstance(mensagem[0], str):
            return "; ".join(m for m in mensagem if isinstance(m, str))
    return ""


def _url_de_api(bruto: str, *, permitir_http: bool) -> str:
    """Mesma validação de `_url_de_api` em `platform/config.py` dos serviços.

    Aceita caminho (`https://api.exemplo/v1`), porque é uma base e não um
    endpoint pronto. Recusa query e fragmento: os dois seriam descartados ou
    duplicados na composição, sem erro visível.
    """
    if not bruto or not bruto.strip():
        raise ValueError("base_url é obrigatória")
    url = urlparse(bruto)
    if url.scheme not in ("http", "https"):
        raise ValueError("base_url deve começar com http:// ou https://")
    if url.scheme == "http" and not permitir_http:
        raise ValueError("base_url exige HTTPS fora de desenvolvimento")
    if not url.hostname:
        raise ValueError("base_url sem host")
    if url.username or url.password:
        raise ValueError("base_url não pode conter credenciais")
    if url.query or url.fragment:
        raise ValueError("base_url não pode conter query nem fragmento")
    return bruto.rstrip("/")
