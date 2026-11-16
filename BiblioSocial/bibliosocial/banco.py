"""Persistência SQLite; nomes de domínio em português."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from flask import current_app, g


def abrir_conexao(caminho: str) -> sqlite3.Connection:
    conexao = sqlite3.connect(caminho, timeout=10)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys=ON")
    conexao.execute("PRAGMA busy_timeout=10000")
    return conexao


def inicializar_banco(caminho: str) -> None:
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conexao = abrir_conexao(caminho)
    try:
        conexao.execute("PRAGMA journal_mode=WAL")
        script = Path(__file__).with_name("esquema.sql").read_text(encoding="utf-8")
        conexao.executescript(script)
        conexao.commit()
    finally:
        conexao.close()


def obter_banco() -> sqlite3.Connection:
    if "banco" not in g:
        g.banco = abrir_conexao(current_app.config["CAMINHO_BANCO"])
    return g.banco


def fechar_banco(erro=None) -> None:
    conexao = g.pop("banco", None)
    if conexao is not None:
        conexao.close()


def consultar_um(conexao: sqlite3.Connection, comando: str, parametros=()):
    return conexao.execute(comando, parametros).fetchone()


def consultar_todos(conexao: sqlite3.Connection, comando: str, parametros=()):
    return conexao.execute(comando, parametros).fetchall()
