"""Cliente HTTP genérico para a API REST/JSON da Omie.

Todas as chamadas da Omie seguem o mesmo envelope:

    POST https://app.omie.com.br/api/v1/<modulo>/
    {
        "call": "<NomeDaChamada>",
        "app_key": "...",
        "app_secret": "...",
        "param": [ { ...parametros... } ]
    }

Em caso de erro a Omie normalmente responde HTTP 200/4xx/5xx com um corpo
contendo "faultstring"/"faultcode". Este cliente centraliza autenticação,
limitação de taxa (rate limit) e retentativas com backoff exponencial.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any

import requests

logger = logging.getLogger("omie_client")

BASE_URL = "https://app.omie.com.br/api/v1"

# Mensagens/códigos que indicam throttling do lado da Omie e justificam retry.
_MENSAGENS_RATE_LIMIT = (
    "muitas requisi",
    "numero de chamadas",
    "número de chamadas",
    "limite de requisi",
    "too many requests",
)


class OmieAPIError(RuntimeError):
    """Erro retornado pela API da Omie (faultstring/faultcode) ou HTTP não recuperável."""

    def __init__(self, message: str, fault_code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.fault_code = fault_code
        self.http_status = http_status


class OmieClient:
    """Cliente HTTP para a Omie. Seguro para uso concorrente (múltiplas threads
    podem compartilhar a mesma instância) — o limitador de taxa serializa apenas
    o *início* de cada chamada (respeitando `max_req_por_segundo` chamadas novas
    por segundo, por processo), enquanto o corpo da requisição (I/O de rede) roda
    em paralelo entre as threads, conforme os limites de concorrência da Omie
    (até 4 requisições simultâneas por IP + App Key + Método)."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        max_req_por_segundo: float = 3.0,
        timeout: int = 60,
        max_retries: int = 5,
        debug_dir: str | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.debug_dir = debug_dir
        self._min_interval = 1.0 / max_req_por_segundo if max_req_por_segundo > 0 else 0.0
        self._last_call_ts = 0.0
        self._rate_lock = threading.Lock()
        self._session = requests.Session()
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)

    def _respeitar_rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        with self._rate_lock:
            decorrido = time.monotonic() - self._last_call_ts
            espera = self._min_interval - decorrido
            if espera > 0:
                time.sleep(espera)
            self._last_call_ts = time.monotonic()

    def call(self, modulo: str, chamada: str, param: dict[str, Any]) -> dict[str, Any]:
        """Executa uma chamada da API Omie e retorna o corpo JSON de resposta.

        modulo: caminho do endpoint, ex. "financas/pesquisartitulos", "geral/clientes".
        chamada: nome da action Omie, ex. "PesquisarLancamentos".
        param: dicionário de parâmetros da chamada (vira o único item da lista "param").
        """
        url = f"{BASE_URL}/{modulo.strip('/')}/"
        payload = {
            "call": chamada,
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "param": [param],
        }

        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.max_retries + 1):
            self._respeitar_rate_limit()
            try:
                resp = self._session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                ultimo_erro = exc
                logger.warning("Falha de rede em %s (tentativa %d/%d): %s", chamada, tentativa, self.max_retries, exc)
                time.sleep(min(2 ** tentativa, 30))
                continue

            self._dump_debug(chamada, tentativa, payload, resp)

            corpo: Any
            try:
                corpo = resp.json()
            except ValueError:
                corpo = {"faultstring": resp.text[:500]}

            if resp.status_code == 200 and isinstance(corpo, dict) and "faultstring" not in corpo:
                return corpo

            fault_string = corpo.get("faultstring", "") if isinstance(corpo, dict) else str(corpo)
            fault_code = corpo.get("faultcode") if isinstance(corpo, dict) else None

            if resp.status_code == 425:
                # A Omie já aplicou o bloqueio de 30 minutos (10 falhas consecutivas
                # para o mesmo IP + App Key + Método). Insistir com retry/backoff
                # curto é inútil e só piora a situação — falha rápido com uma
                # mensagem acionável em vez de consumir as tentativas.
                raise OmieAPIError(
                    f"Chamada '{chamada}' bloqueada pela Omie por excesso de requisições (HTTP 425). "
                    "Isso ocorre após 10 falhas consecutivas para o mesmo método e dura ~30 minutos. "
                    "Aguarde antes de tentar novamente; considere reduzir OMIE_MAX_REQ_POR_SEGUNDO.",
                    fault_code=fault_code,
                    http_status=425,
                )

            recuperavel = resp.status_code in (429, 500, 502, 503, 504) or any(
                token in fault_string.lower() for token in _MENSAGENS_RATE_LIMIT
            )

            if recuperavel and tentativa < self.max_retries:
                espera = min(2 ** tentativa, 30)
                logger.warning(
                    "Erro recuperável em %s (HTTP %s, tentativa %d/%d): %s — aguardando %.1fs",
                    chamada, resp.status_code, tentativa, self.max_retries, fault_string, espera,
                )
                time.sleep(espera)
                ultimo_erro = OmieAPIError(fault_string, fault_code, resp.status_code)
                continue

            raise OmieAPIError(
                f"Chamada '{chamada}' falhou (HTTP {resp.status_code}): {fault_string}",
                fault_code=fault_code,
                http_status=resp.status_code,
            )

        raise OmieAPIError(f"Chamada '{chamada}' falhou após {self.max_retries} tentativas: {ultimo_erro}")

    def _dump_debug(self, chamada: str, tentativa: int, payload: dict, resp: requests.Response) -> None:
        if not self.debug_dir:
            return
        ts = time.strftime("%Y%m%d-%H%M%S")
        sufixo = uuid.uuid4().hex[:6]
        nome = f"{ts}_{chamada}_try{tentativa}_{sufixo}.json"
        caminho = os.path.join(self.debug_dir, nome)
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(
                    {"request": payload, "status_code": resp.status_code, "response": self._safe_json(resp)},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            logger.debug("Não foi possível gravar debug dump em %s", caminho)

    @staticmethod
    def _safe_json(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return resp.text[:2000]
