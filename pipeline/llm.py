"""Вызовы модели через Claude Code CLI (`claude -p`) — работает на подписке, без платного API.

Каждый вызов: один аккаунт, фиксированный промпт, ответ строго JSON по pydantic-схеме.
Невалидный ответ код отклоняет и повторяет запрос с текстом ошибки.

Флаги CLI сверены с Claude Code 2.x. Если ваша версия отличается — проверьте `claude --help`
и поправьте build_cmd(): нужны вывод в JSON (`--output-format json`), выбор модели (`--model`)
и список разрешённых инструментов (`--allowedTools`).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from typing import Protocol, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class Runner(Protocol):
    def run(self, prompt: str, *, model: str, tools: list[str], timeout: int) -> str: ...


class ClaudeCLI:
    def __init__(self, binary: str = "claude"):
        self.binary = binary

    def build_cmd(self, model: str, tools: list[str]) -> list[str]:
        cmd = [self.binary, "-p", "--output-format", "json", "--model", model]
        if tools:
            cmd += ["--allowedTools", ",".join(tools)]
        return cmd

    def run(self, prompt: str, *, model: str, tools: list[str], timeout: int) -> str:
        # Пустая рабочая папка: модели нечего читать или менять вокруг.
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run(self.build_cmd(model, tools), input=prompt, text=True,
                               capture_output=True, timeout=timeout, cwd=cwd)
        if p.returncode != 0:
            raise RuntimeError(f"claude -p exit {p.returncode}: {p.stderr.strip()[:500]}")
        try:
            env = json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.stdout
        if env.get("is_error"):
            raise RuntimeError(f"claude -p error: {str(env.get('result'))[:500]}")
        return env.get("result", "")


def extract_json(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("в ответе нет JSON-объекта")
    return json.loads(text[s: e + 1])


def call_json(runner: Runner, prompt: str, schema: Type[T], *, model: str, tools: list[str] | None = None,
              timeout: int = 300, retries: int = 2) -> T:
    tools = tools or []
    last_err = ""
    for attempt in range(retries + 1):
        p = prompt if not last_err else (
            prompt + "\n\n# ПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН\n" + last_err
            + "\nВерни только исправленный JSON по схеме, без пояснений."
        )
        out = runner.run(p, model=model, tools=tools, timeout=timeout)
        try:
            return schema.model_validate(extract_json(out))
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:1500]
    raise ValueError(f"модель не вернула валидный {schema.__name__}: {last_err}")
