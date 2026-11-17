"""Fila persistente de comandos enviados pelo painel para a ESP32."""
from __future__ import annotations

import re
import secrets
import sqlite3
import time


TIPOS_COMANDO = frozenset({
    "led_verde",
    "led_vermelho",
    "buzzer",
    "sinal_sucesso",
    "sinal_erro",
})
_PADRAO_DISPOSITIVO = re.compile(r"[A-Za-z0-9._:-]{1,80}")


class ErroComando(ValueError):
    """Erro de validação ou de transição da fila de comandos."""


def normalizar_dispositivo(valor: object) -> str:
    dispositivo = str(valor or "").strip()
    if not _PADRAO_DISPOSITIVO.fullmatch(dispositivo):
        raise ErroComando("Identificador de dispositivo inválido.")
    return dispositivo


def expirar_comandos(conexao: sqlite3.Connection, agora: int | None = None) -> int:
    instante = int(time.time()) if agora is None else int(agora)
    cursor = conexao.execute(
        """UPDATE comandos_dispositivo
           SET estado='expirado', concluido_em=?
           WHERE estado IN ('pendente','entregue') AND expira_em<=?""",
        (instante, instante),
    )
    return int(cursor.rowcount)


def criar_comando(
    conexao: sqlite3.Connection,
    tipo: object,
    dispositivo: object,
    *,
    origem: str = "painel",
    criado_por: str | None = None,
    prazo_segundos: int = 45,
    agora: int | None = None,
) -> dict:
    tipo_normalizado = str(tipo or "").strip().lower()
    if tipo_normalizado not in TIPOS_COMANDO:
        raise ErroComando("Tipo de comando não permitido.")
    dispositivo_normalizado = normalizar_dispositivo(dispositivo)
    if origem not in ("painel", "simulacao"):
        raise ErroComando("Origem de comando inválida.")
    prazo = max(10, min(int(prazo_segundos), 300))
    instante = int(time.time()) if agora is None else int(agora)
    identificador = secrets.token_urlsafe(24)
    conexao.execute(
        """INSERT INTO comandos_dispositivo
           (id,dispositivo,tipo,estado,origem,criado_por,criado_em,expira_em)
           VALUES(?,?,?,'pendente',?,?,?,?)""",
        (
            identificador,
            dispositivo_normalizado,
            tipo_normalizado,
            origem,
            (str(criado_por).strip()[:80] if criado_por else None),
            instante,
            instante + prazo,
        ),
    )
    return consultar_comando(conexao, identificador, expirar=False)


def consultar_comando(
    conexao: sqlite3.Connection, identificador: str, *, expirar: bool = True
) -> dict | None:
    if expirar:
        expirar_comandos(conexao)
    registro = conexao.execute(
        "SELECT * FROM comandos_dispositivo WHERE id=?", (identificador,)
    ).fetchone()
    return dict(registro) if registro else None


def entregar_proximo_comando(
    conexao: sqlite3.Connection, dispositivo: object, agora: int | None = None
) -> dict | None:
    """Entrega um comando por vez e repete o entregue até receber confirmação.

    A repetição permite recuperar perda de resposta HTTPS. A ESP32 deve guardar o
    último ID concluído e apenas reenviar a confirmação, sem executar novamente.
    """
    dispositivo_normalizado = normalizar_dispositivo(dispositivo)
    instante = int(time.time()) if agora is None else int(agora)
    conexao.execute("BEGIN IMMEDIATE")
    try:
        expirar_comandos(conexao, instante)
        conexao.execute(
            """INSERT INTO dispositivos(identificador) VALUES(?)
               ON CONFLICT(identificador) DO UPDATE
               SET ultima_atividade=datetime('now')""",
            (dispositivo_normalizado,),
        )
        registro = conexao.execute(
            """SELECT * FROM comandos_dispositivo
               WHERE dispositivo=? AND estado='entregue'
               ORDER BY criado_em,rowid LIMIT 1""",
            (dispositivo_normalizado,),
        ).fetchone()
        if registro is None:
            registro = conexao.execute(
                """SELECT * FROM comandos_dispositivo
                   WHERE dispositivo=? AND estado='pendente'
                   ORDER BY criado_em,rowid LIMIT 1""",
                (dispositivo_normalizado,),
            ).fetchone()
            if registro is not None:
                conexao.execute(
                    """UPDATE comandos_dispositivo
                       SET estado='entregue', entregue_em=?, tentativas=tentativas+1
                       WHERE id=? AND estado='pendente'""",
                    (instante, registro["id"]),
                )
                registro = conexao.execute(
                    "SELECT * FROM comandos_dispositivo WHERE id=?", (registro["id"],)
                ).fetchone()
        conexao.commit()
        return dict(registro) if registro else None
    except Exception:
        conexao.rollback()
        raise


def confirmar_comando(
    conexao: sqlite3.Connection,
    identificador: str,
    dispositivo: object,
    resultado: object,
    detalhe: object = "",
    agora: int | None = None,
) -> dict:
    dispositivo_normalizado = normalizar_dispositivo(dispositivo)
    estado_final = str(resultado or "").strip().lower()
    if estado_final not in ("executado", "falhou"):
        raise ErroComando("Resultado deve ser 'executado' ou 'falhou'.")
    instante = int(time.time()) if agora is None else int(agora)
    conexao.execute("BEGIN IMMEDIATE")
    try:
        expirar_comandos(conexao, instante)
        registro = conexao.execute(
            "SELECT * FROM comandos_dispositivo WHERE id=?", (identificador,)
        ).fetchone()
        if registro is None:
            raise ErroComando("Comando não encontrado.")
        if registro["dispositivo"] != dispositivo_normalizado:
            raise ErroComando("Comando destinado a outro dispositivo.")
        if registro["estado"] in ("executado", "falhou"):
            conexao.commit()
            return dict(registro)
        if registro["estado"] == "expirado":
            conexao.commit()
            raise ErroComando("Comando expirado; não deve ser executado.")
        if registro["estado"] != "entregue":
            raise ErroComando("Comando ainda não foi entregue ao dispositivo.")
        conexao.execute(
            """UPDATE comandos_dispositivo
               SET estado=?, detalhe=?, concluido_em=? WHERE id=?""",
            (estado_final, str(detalhe or "").strip()[:500] or None, instante, identificador),
        )
        conexao.execute(
            "UPDATE dispositivos SET ultima_atividade=datetime('now') WHERE identificador=?",
            (dispositivo_normalizado,),
        )
        registro = conexao.execute(
            "SELECT * FROM comandos_dispositivo WHERE id=?", (identificador,)
        ).fetchone()
        conexao.commit()
        return dict(registro)
    except Exception:
        conexao.rollback()
        raise


def sinalizacao_simulacao_ativa(conexao: sqlite3.Connection) -> bool:
    registro = conexao.execute(
        "SELECT valor FROM configuracoes WHERE chave='sinalizacao_fisica_simulacao'"
    ).fetchone()
    return bool(registro and registro["valor"] == "1")


def definir_sinalizacao_simulacao(conexao: sqlite3.Connection, ativa: bool) -> None:
    conexao.execute(
        """INSERT INTO configuracoes(chave,valor,atualizado_em)
           VALUES('sinalizacao_fisica_simulacao',?,datetime('now'))
           ON CONFLICT(chave) DO UPDATE
           SET valor=excluded.valor, atualizado_em=excluded.atualizado_em""",
        ("1" if ativa else "0",),
    )
