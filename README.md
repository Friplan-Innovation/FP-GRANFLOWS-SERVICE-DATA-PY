# granflows-service-data

Cliente Python da **GranFlows Data API** — o plano de dados dos serviços de
negócio, com isolamento por usuário.

Uma implementação, versionada por tag, que os serviços instalam. O pacote existe
para que o contrato de erros e as garantias de falha sejam escritos **uma vez** e
não uma vez por serviço: quando cada um traz a sua própria camada HTTP, elas
divergem, e a divergência só aparece no serviço que ficou para trás.

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

# por REQUISIÇÃO — o token de escopo de quem está usando o sistema.
sessao = cliente.para(identidade.data_api_token)
itens = sessao.get("/v1/exemplo/itens")
```

Os verbos existem **só** em `SessaoDataApi`, obtida por `cliente.para(token)`.
Não há caminho que chame a API sem escopo — a porta é única e o token é
obrigatório para atravessá-la.

O token é emitido pelo plano de controle e vive na sessão server-side do próprio
serviço. **Nunca** vem de parâmetro de rota, corpo ou cabeçalho recebido do
navegador.

## O contrato de erros

Cada status vira exatamente uma exceção, e é essa tradução que decide o que o
serviço responde:

| Exceção | Quando | O serviço responde |
|---|---|---|
| `DataApiIndisponivel` | timeout, erro de rede, 5xx, 3xx, 2xx que não é JSON | **503** |
| `NaoAutorizado` | 401, 403 — o token venceu ou foi recusado | **401**, e renova a sessão |
| `RecursoNaoEncontrado` | 404 | **404**, nunca 403 |
| `Conflito` | 409 — colisão com o estado atual | **409** |
| `EntradaInvalida` | 400, 422 | **400** |

`RecursoNaoEncontrado` é a que mais importa acertar. A API responde 404 tanto
para o que não existe quanto para o que é de outro dono, **de propósito** — um
403 confirmaria que aquele identificador existe e pertence a outra pessoa.
Traduzir para 403 no serviço desfaria a proteção do outro lado.

## Três garantias

**Falha fechada.** Não existe caminho neste pacote que devolva lista vazia,
`None` ou valor default quando a API não respondeu. Uma lista vazia é
indistinguível de um histórico apagado, e é assim que se perde dado sem ninguém
perceber. Quem captura `DataApiIndisponivel` responde 503 — nunca degrada para
armazenamento local, para memória, nem para "não tem nada".

**Sem retry automático.** Uma escrita retentada às cegas é um registro duplicado
esperando a restrição de unicidade recusar, e o timeout não diz se a primeira
chegou. Para leitura o retry seria seguro, mas esconder latência atrás de
tentativas silenciosas faz a degradação aparecer como lentidão em vez de erro —
e aí ninguém investiga.

**O token não vaza.** Não aparece em mensagem de exceção, em `repr`, nem em log.
Viaja no cabeçalho `Authorization` e não é interpolado em string nenhuma — há
teste que reprova se alguém remover o `__repr__` explícito, porque o default do
Python exporia os atributos numa stack trace.

## A URL base vem de configuração

Nunca de nada que chegue numa requisição. É o que fecha SSRF. O construtor
valida o formato e recusa credencial embutida, query, fragmento e `http://` fora
de desenvolvimento.

Também não segue redirect: seguir um `302` cegamente mandaria o `Authorization`
para onde o `Location` apontasse. Um 3xx é tratado como indisponibilidade — a
API não redireciona, então um redirect ali significa proxy no caminho ou ingress
mal configurado.

## Desenvolvimento

Sem Python instalado? Os testes rodam em container:

```bash
docker run --rm -v "$(pwd)":/pkg -w /pkg python:3.11-slim \
  sh -c "pip install -q -e . pytest mypy ruff && python -m pytest -q && mypy granflows_service_data && ruff check ."
```

Os testes usam `httpx.MockTransport` — não sobem servidor e **não importam
código de nenhum serviço consumidor**. É a propriedade que define "pacote": se
precisassem de um serviço no caminho, isto seria uma pasta daquele serviço com
outro nome.

## Versionamento

`MAJOR.MINOR.PATCH`. Mudança no contrato de erros — uma exceção nova, ou um
status que passa a mapear para outra — é **MAJOR**, porque cada serviço traduz
essas exceções em resposta HTTP e a tradução silenciosamente errada é o pior
desfecho possível.
