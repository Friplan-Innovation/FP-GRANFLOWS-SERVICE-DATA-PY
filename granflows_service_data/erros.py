"""
Os erros que a Data API produz, traduzidos para o vocabulário do serviço.

**Por que uma hierarquia e não uma exceção só.** Cada uma destas vira uma
resposta HTTP diferente no microserviço que chama, e a tradução precisa ser
mecânica — não uma decisão que cada rota toma de novo:

    DataApiIndisponivel  -> 503   o serviço está degradado, não é culpa de quem pediu
    NaoAutorizado        -> 401   o scopeToken venceu; refazer o protocolo de launch
    RecursoNaoEncontrado -> 404   não existe, OU não é seu — nunca 403
    Conflito             -> 409   resposta esperada, não defeito
    EntradaInvalida      -> 400   o serviço montou uma requisição que a API recusou

`RecursoNaoEncontrado` é a que mais importa acertar. A Data API responde 404
tanto para o que não existe quanto para o que é de outro dono, de propósito —
um 403 confirmaria que aquele identificador existe e pertence a outra pessoa.
Traduzir isso para 403 no serviço desfaria a proteção do outro lado.
"""

from __future__ import annotations


class ErroDaDataApi(Exception):
    """Raiz. Existe para `except ErroDaDataApi` pegar tudo de uma vez."""


class DataApiIndisponivel(ErroDaDataApi):
    """Não deu para FALAR com a Data API, ou ela respondeu 5xx.

    **Falha fechada.** Quem captura isto responde 503 e não grava nada em
    lugar nenhum — nunca degrada para SQLite, para memória, nem devolve lista
    vazia como se fosse "não tem nada". Uma lista vazia é indistinguível de um
    histórico apagado, e é assim que se perde dado sem ninguém perceber.
    """


class NaoAutorizado(ErroDaDataApi):
    """O scopeToken foi recusado — vencido, malformado ou de outro serviço.

    Não é erro de quem está usando o sistema: o token tem validade curta
    (dezenas de minutos) e vencer é o caso normal. O serviço deve refazer o
    protocolo de launch, que reemite tudo e revalida o acesso ao vivo.
    """


class RecursoNaoEncontrado(ErroDaDataApi):
    """404 da Data API — não existe, ou não é do dono desta sessão.

    Os dois casos são o MESMO caso aqui, e é assim que tem que ser. Distinguir
    exigiria a API dizer "existe, mas não é seu", que é exatamente a
    informação que o isolamento por usuário existe para não dar.
    """


class Conflito(ErroDaDataApi):
    """409 — a operação colidiu com o estado atual.

    Emissão já registrada (a idempotência funcionando), teto de mensagens de
    job atingido. É resposta ESPERADA, não defeito: quem captura decide o que
    fazer e normalmente não loga como erro.
    """


class EntradaInvalida(ErroDaDataApi):
    """400 ou 422 — a Data API recusou o corpo da requisição.

    Quase sempre defeito de programação no serviço que chamou, não algo que a
    pessoa usando o sistema fez: o `ValidationPipe` da API roda com
    `forbidNonWhitelisted`, então um campo desconhecido no corpo é recusado em
    vez de removido em silêncio.
    """
