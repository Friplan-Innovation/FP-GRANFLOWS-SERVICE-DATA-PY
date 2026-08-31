# granflows-service-data

Cliente Python da **Data API dos microserviços GranFlows** — o plano de dados
separado, com isolamento por usuário e dado padronizado para busca e BI.

## Por que este pacote existe

Os seis microserviços têm o mesmo módulo `platform/` copiado entre si, e ele
**já divergiu**: 81 linhas de diferença entre o `session_store.py` do Book de
Planejamento e o do Data Book, contra 12 entre Data Book e Mapa de Juntas. São
duas gerações do mesmo arquivo convivendo, e o piloto está na antiga.

Escrever o cliente da Data API seis vezes produziria seis gerações do mesmo
defeito. Este pacote é a alternativa: uma implementação, versionada por tag, que
os seis instalam.

## Instalação

```
granflows-service-data @ git+https://github.com/Friplan-Innovation/FP-GRANFLOWS-SERVICE-DATA-PY.git@v0.1.0
```

Sempre com **tag**, nunca com branch. Um serviço em produção não deve mudar de
comportamento porque alguém mergeou algo aqui.

## Uso

Duas camadas, de propósito:

```python
from granflows_service_data import ClienteDataApi

# no BOOT — uma vez por processo. Guarda o pool de conexões.
cliente = ClienteDataApi(cfg.data_api_url)

# por REQUISIÇÃO — o scopeToken de quem está usando o sistema.
sessao = cliente.para(identidade.scope_token)
jobs = sessao.get("/v1/book/jobs")
```

Os verbos só existem em `SessaoDataApi`, então **não há caminho que chame a Data
API sem escopo**. É a mesma ideia do `withScope()` do lado da API: a porta é
única e o escopo é obrigatório para atravessá-la.

O `scope_token` vem do `dataApiToken` que o exchange do plano de controle
devolve, guardado na sessão server-side em Redis do próprio serviço. **Nunca**
de parâmetro de rota, corpo ou cabeçalho recebido do navegador.

## O contrato de erros

Cada status vira exatamente uma exceção, e é essa tradução que decide o que o
microserviço responde:

| Exceção | Quando | O serviço responde |
|---|---|---|
| `DataApiIndisponivel` | timeout, erro de rede, 5xx, 2xx que não é JSON | **503** |
| `NaoAutorizado` | 401, 403 — o scopeToken venceu ou foi recusado | **401**, e refaz o launch |
| `RecursoNaoEncontrado` | 404 | **404**, nunca 403 |
| `Conflito` | 409 — emissão já registrada, teto de mensagens | **409** |
| `EntradaInvalida` | 400, 422 | **400** |

`RecursoNaoEncontrado` é a que mais importa acertar. A Data API responde 404
tanto para o que não existe quanto para o que é de outro dono, **de propósito** —
um 403 confirmaria que aquele identificador existe e pertence a outra pessoa.
Traduzir para 403 no serviço desfaria a proteção do outro lado.

## Três garantias

**Falha fechada.** Não existe caminho neste pacote que devolva lista vazia,
`None` ou valor default quando a Data API não respondeu. Uma lista vazia é
indistinguível de um histórico apagado, e é assim que se perde dado sem ninguém
perceber. Quem captura `DataApiIndisponivel` responde 503 — nunca degrada para
SQLite, para memória, nem para "não tem nada".

**Sem retry automático.** Uma escrita retentada às cegas é uma emissão duplicada
esperando o `UNIQUE` do banco recusar, e o timeout não diz se a primeira chegou.
Para leitura o retry seria seguro, mas esconder latência atrás de tentativas
silenciosas faz a degradação aparecer como lentidão em vez de como erro — e aí
ninguém investiga.

**O token nunca vaza.** Não aparece em mensagem de exceção, em `repr`, nem em
log. Viaja no cabeçalho `Authorization` e não é interpolado em string nenhuma —
há teste que reprova se alguém remover o `__repr__` explícito, porque o default
do Python exporia os atributos numa stack trace.

## A URL base vem de configuração

Nunca de nada que chegue numa requisição. É o que fecha SSRF, e é a mesma regra
que `platform/config.py` dos serviços já aplica ao `platform_api_url`. O
construtor valida o formato e recusa credencial embutida, query, fragmento e
`http://` fora de desenvolvimento.

Também não segue redirect: seguir um `302` cegamente mandaria o `Authorization`
para onde o `Location` apontasse.

## Desenvolvimento

Sem Python instalado na máquina? Os testes rodam em container, como os dos
serviços:

```bash
docker run --rm -v "$(pwd -W)":/pkg -w /pkg python:3.11-slim \
  sh -c "pip install -q -e . pytest mypy ruff && python -m pytest -q && mypy . && ruff check ."
```

Os testes usam `httpx.MockTransport` — não sobem servidor e **não importam
nenhum código de serviço**. É a propriedade que define "pacote": se precisassem
do Book no caminho, isto seria uma pasta do Book com outro nome, e a próxima
divergência entre os seis já estaria plantada.
