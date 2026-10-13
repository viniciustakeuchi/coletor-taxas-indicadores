import httpx
import pytest

from coletor.retry import PoliticaRetry, com_retry, erro_transitorio_http


class Dormidor:
    def __init__(self) -> None:
        self.esperas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


def erro_status(status: int) -> httpx.HTTPStatusError:
    requisicao = httpx.Request("GET", "https://exemplo")
    return httpx.HTTPStatusError(
        "erro", request=requisicao, response=httpx.Response(status, request=requisicao)
    )


def test_espera_cresce_exponencialmente_ate_o_teto() -> None:
    politica = PoliticaRetry(tentativas=6, backoff_inicial=1, backoff_maximo=5)
    tetos = [politica.espera(n, aleatorio=lambda: 1.0) for n in range(1, 6)]
    assert tetos == [1, 2, 4, 5, 5]


def test_jitter_fica_entre_zero_e_o_teto() -> None:
    politica = PoliticaRetry(backoff_inicial=2, backoff_maximo=30)
    assert politica.espera(3, aleatorio=lambda: 0.0) == 0
    assert politica.espera(3, aleatorio=lambda: 0.5) == 4


async def test_repete_ate_dar_certo_e_conta_tentativas() -> None:
    falhas = [httpx.ConnectError("caiu"), erro_status(503)]
    dormir = Dormidor()

    async def operacao() -> str:
        if falhas:
            raise falhas.pop(0)
        return "ok"

    resultado = await com_retry(
        operacao,
        politica=PoliticaRetry(tentativas=4),
        repetir_se=erro_transitorio_http,
        dormir=dormir,
    )
    assert resultado.valor == "ok"
    assert resultado.tentativas == 3
    assert len(dormir.esperas) == 2


async def test_desiste_depois_da_ultima_tentativa() -> None:
    dormir = Dormidor()

    async def operacao() -> str:
        raise httpx.ReadTimeout("lento")

    with pytest.raises(httpx.ReadTimeout):
        await com_retry(
            operacao,
            politica=PoliticaRetry(tentativas=3),
            repetir_se=erro_transitorio_http,
            dormir=dormir,
        )
    assert len(dormir.esperas) == 2


async def test_erro_definitivo_nao_e_repetido() -> None:
    chamadas = 0
    dormir = Dormidor()

    async def operacao() -> str:
        nonlocal chamadas
        chamadas += 1
        raise erro_status(400)

    with pytest.raises(httpx.HTTPStatusError):
        await com_retry(
            operacao,
            politica=PoliticaRetry(tentativas=4),
            repetir_se=erro_transitorio_http,
            dormir=dormir,
        )
    assert chamadas == 1
    assert dormir.esperas == []


@pytest.mark.parametrize(
    ("erro", "esperado"),
    [
        (httpx.ConnectTimeout("t"), True),
        (erro_status(429), True),
        (erro_status(500), True),
        (erro_status(502), True),
        (erro_status(400), False),
        (erro_status(404), False),
        (ValueError("x"), False),
    ],
)
def test_classificacao_de_erros_http(erro: BaseException, esperado: bool) -> None:
    assert erro_transitorio_http(erro) is esperado
