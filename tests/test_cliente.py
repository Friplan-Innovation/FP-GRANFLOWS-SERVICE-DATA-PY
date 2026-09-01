"""
O contrato do cliente, exercitado sem servidor e sem nenhum código de serviço.

**Por que sem código de serviço.** O pacote só está pronto quando for
importável de fora — se estes testes precisassem de um serviço consumidor no
caminho, o "pacote" seria uma pasta daquele serviço com outro nome, e a próxima
divergência entre consumidores já estaria plantada.

`httpx.MockTransport` deixa afirmar a tradução de status em exceção sem subir
nada. O que interessa aqui não é o HTTP — é que **cada status vira exatamente
uma exceção**, porque é essa tradução que decide se o serviço responde 503 ou
404, e errar isso desfaz garantias que a API construiu do outro lado.
"""

from __future__ import annotations

import io

import httpx
import pytest

from granflows_service_data import (
    ClienteDataApi,
    Conflito,
    DataApiIndisponivel,
    EntradaInvalida,
    NaoAutorizado,
    RecursoNaoEncontrado,
)

TOKEN = "token-de-teste-que-nunca-deve-aparecer-em-lugar-nenhum"
BASE = "https://dataapi.interno"


def cliente_com(handler) -> ClienteDataApi:  # type: ignore[no-untyped-def]
    return ClienteDataApi(BASE, transport=httpx.MockTransport(handler))


def responde(status: int, corpo: object = None) -> object:  # type: ignore[no-untyped-def]
    def handler(_req: httpx.Request) -> httpx.Response:
        if corpo is None:
            return httpx.Response(status)
        return httpx.Response(status, json=corpo)

    return handler


# ── a URL base ──────────────────────────────────────────────────────────────


class TestUrlBase:
    """Vem de configuração, nunca de requisição — é o que fecha SSRF."""

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "ftp://dataapi.interno",
            "dataapi.interno",
            "https://",
            "https://user:senha@dataapi.interno",
            "https://dataapi.interno?x=1",
            "https://dataapi.interno#frag",
        ],
    )
    def test_recusa_url_invalida(self, url: str) -> None:
        with pytest.raises(ValueError):
            ClienteDataApi(url)

    def test_recusa_http_por_padrao(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            ClienteDataApi("http://dataapi.interno")

    def test_aceita_http_quando_liberado_explicitamente(self) -> None:
        # O escape hatch do desenvolvimento local. Explícito, nunca por default.
        cliente = ClienteDataApi("http://localhost:3000", permitir_http=True)
        assert cliente.base_url == "http://localhost:3000"

    def test_aceita_caminho_como_base(self) -> None:
        assert ClienteDataApi("https://dataapi.interno/v1/").base_url == "https://dataapi.interno/v1"


# ── a sessão ────────────────────────────────────────────────────────────────


class TestSessao:
    def test_nao_ha_verbo_sem_token(self) -> None:
        # A propriedade que faz o desenho valer: os verbos só existem em
        # `SessaoDataApi`, e a única forma de obter uma é `para(token)`.
        cliente = cliente_com(responde(200, {}))
        assert not hasattr(cliente, "get")
        assert not hasattr(cliente, "post")

    def test_recusa_token_vazio(self) -> None:
        cliente = cliente_com(responde(200, {}))
        with pytest.raises(ValueError, match="scope_token"):
            cliente.para("   ")

    def test_manda_o_token_no_authorization(self) -> None:
        visto: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            visto["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, json={"ok": True})

        cliente_com(handler).para(TOKEN).get("/v1/exemplo/itens")
        assert visto["auth"] == f"Bearer {TOKEN}"


# ── a tradução de status em exceção ─────────────────────────────────────────


class TestTraducaoDeErro:
    """Cada status vira exatamente uma exceção. É o coração do pacote."""

    @pytest.mark.parametrize(
        ("status", "esperado"),
        [
            (401, NaoAutorizado),
            (403, NaoAutorizado),
            (404, RecursoNaoEncontrado),
            (409, Conflito),
            (400, EntradaInvalida),
            (422, EntradaInvalida),
            (500, DataApiIndisponivel),
            (502, DataApiIndisponivel),
            (503, DataApiIndisponivel),
            (418, DataApiIndisponivel),
        ],
    )
    def test_status_vira_excecao(self, status: int, esperado: type[Exception]) -> None:
        sessao = cliente_com(responde(status, {"message": "detalhe"})).para(TOKEN)
        with pytest.raises(esperado):
            sessao.get("/v1/exemplo/itens")

    def test_404_nao_vira_403(self) -> None:
        # A Data API responde 404 tanto para "não existe" quanto para "não é
        # seu", de propósito — 403 confirmaria que o identificador existe e é
        # de outra pessoa. Traduzir para 403 aqui desfaria a proteção do outro
        # lado.
        sessao = cliente_com(responde(404)).para(TOKEN)
        with pytest.raises(RecursoNaoEncontrado):
            sessao.get("/v1/exemplo/itens/qualquer-id")

    def test_conflito_carrega_a_frase_da_api(self) -> None:
        # A frase da API entra na exceção porque o serviço às vezes a repassa a
        # quem chamou — 409 é resposta esperada, não defeito.
        sessao = cliente_com(responde(409, {"message": "emissão já registrada"})).para(TOKEN)
        with pytest.raises(Conflito, match="emissão já registrada"):
            sessao.post("/v1/exemplo/registros", {"numero": "REG-1"})

    def test_mensagem_de_erro_nao_contem_o_token(self) -> None:
        # A garantia que mais importa deste arquivo. O token viaja em cabeçalho
        # e nunca é interpolado em string nenhuma — nem em exceção, nem em log.
        sessao = cliente_com(responde(500, {"message": "boom"})).para(TOKEN)
        with pytest.raises(DataApiIndisponivel) as capturado:
            sessao.get("/v1/exemplo/itens")
        assert TOKEN not in str(capturado.value)
        assert TOKEN not in repr(capturado.value)


# ── falha fechada ───────────────────────────────────────────────────────────


class TestFalhaFechada:
    """Nunca degrada. Sem lista vazia, sem None, sem valor default."""

    def test_timeout_vira_indisponivel(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("estourou", request=req)

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel, match="timeout"):
            sessao.get("/v1/exemplo/itens")

    def test_erro_de_rede_vira_indisponivel(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("recusou", request=req)

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel, match="erro de rede"):
            sessao.get("/v1/exemplo/itens")

    def test_lista_vazia_nunca_substitui_indisponibilidade(self) -> None:
        # Uma lista vazia é indistinguível de um histórico apagado. Se a API
        # não respondeu, o serviço tem que saber disso.
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("recusou", request=req)

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel):
            resultado = sessao.get("/v1/exemplo/registros")
            assert resultado != []  # nunca alcançado; documenta a intenção

    def test_2xx_que_nao_e_json_falha_fechado(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>proxy no meio do caminho</html>")

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel, match="não é JSON"):
            sessao.get("/v1/exemplo/itens")

    def test_sem_retry_em_escrita(self) -> None:
        # Uma escrita retentada às cegas é uma emissão duplicada esperando o
        # UNIQUE do banco recusar — o timeout não diz se a primeira chegou.
        tentativas = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            raise httpx.ConnectTimeout("estourou", request=req)

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel):
            sessao.post("/v1/exemplo/registros", {"numero": "REG-1"})
        assert tentativas["n"] == 1

    def test_nao_segue_redirect(self) -> None:
        # Seguir um redirect cegamente mandaria o `Authorization` para onde o
        # Location apontasse.
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://outro.lugar/roubar"})

        sessao = cliente_com(handler).para(TOKEN)
        with pytest.raises(DataApiIndisponivel):
            sessao.get("/v1/exemplo/itens")


# ── o caminho feliz ─────────────────────────────────────────────────────────


class TestCaminhoFeliz:
    def test_get_devolve_o_json(self) -> None:
        sessao = cliente_com(responde(200, [{"id": "a"}, {"id": "b"}])).para(TOKEN)
        assert sessao.get("/v1/exemplo/itens") == [{"id": "a"}, {"id": "b"}]

    def test_get_manda_os_params(self) -> None:
        visto: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            visto.update(dict(req.url.params))
            return httpx.Response(200, json=[])

        sessao = cliente_com(handler).para(TOKEN)
        sessao.get("/v1/exemplo/itens", params={"identificacao": "REF-4410"})
        assert visto == {"identificacao": "REF-4410"}

    def test_post_manda_o_corpo(self) -> None:
        visto: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            visto.update(_json.loads(req.content))
            return httpx.Response(201, json={"id": "novo"})

        sessao = cliente_com(handler).para(TOKEN)
        resposta = sessao.post("/v1/exemplo/itens", {"estado": "pendente"})
        assert visto == {"estado": "pendente"}
        assert resposta == {"id": "novo"}

    def test_204_devolve_none(self) -> None:
        sessao = cliente_com(responde(204)).para(TOKEN)
        assert sessao.patch("/v1/exemplo/itens/x", {"progresso": 50}) is None

    def test_envio_de_arquivo(self) -> None:
        visto: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            visto["tipo"] = req.headers.get("content-type", "")
            visto["tamanho"] = len(req.content)
            return httpx.Response(201, json={"id": "artefato"})

        resposta = cliente_com(handler).para(TOKEN).enviar_arquivo(
            "/v1/exemplo/arquivos",
            campo="arquivo",
            nome="documento.pdf",
            conteudo=io.BytesIO(b"%PDF-1.7 conteudo"),
            tipo="application/pdf",
            campos={"categoria": "pdf"},
        )
        assert str(visto["tipo"]).startswith("multipart/form-data")
        assert resposta == {"id": "artefato"}

    def test_baixar_devolve_bytes(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.7 bytes")

        assert cliente_com(handler).para(TOKEN).baixar("/v1/exemplo/arquivos/x/conteudo") == (
            b"%PDF-1.7 bytes"
        )

    def test_baixar_traduz_erro_igual(self) -> None:
        sessao = cliente_com(responde(404)).para(TOKEN)
        with pytest.raises(RecursoNaoEncontrado):
            sessao.baixar("/v1/exemplo/arquivos/x/conteudo")


# ── o repr ──────────────────────────────────────────────────────────────────


def test_repr_nao_vaza_o_token() -> None:
    # Sem `__repr__` explícito, o default do Python exporia os atributos numa
    # stack trace. Este teste é o que impede alguém de removê-lo.
    sessao = cliente_com(responde(200, {})).para(TOKEN)
    assert TOKEN not in repr(sessao)
    assert "dataapi.interno" in repr(sessao)
