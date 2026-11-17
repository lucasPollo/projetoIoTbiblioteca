"""Regras de negócio: cadastro, empréstimos e processamento das leituras RFID."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from datetime import datetime
from typing import Mapping

from .banco import consultar_um

PRAZO_LEITURA_RESERVA = 150  # Tempo para aproximar o livro e o cartão


class ErroNegocio(ValueError):
    """Erro que pode ser apresentado ao usuário."""


def normalizar_uid(valor: object, obrigatorio: bool = False) -> str | None:
    texto = re.sub(r"[\s:\-_]", "", str(valor or "")).upper()
    if not texto:
        if obrigatorio:
            raise ErroNegocio("Informe o UID da etiqueta/cartão.")
        return None
    if not re.fullmatch(r"[0-9A-F]{8}(?:[0-9A-F]{6})?(?:[0-9A-F]{6})?", texto):
        raise ErroNegocio("UID inválido: use 8, 14 ou 20 caracteres hexadecimais.")
    return texto


def normalizar_codigo(valor: object, prefixo: str) -> str:
    codigo = str(valor or "").strip().upper()
    if not re.fullmatch(rf"{prefixo}[0-9]{{1,5}}", codigo):
        raise ErroNegocio(f"O código deve seguir o padrão {prefixo}001 ou {prefixo}01.")
    return codigo


def proximo_codigo(conexao, tabela: str, prefixo: str, digitos=3) -> str:
    assert tabela in ("livros", "usuarios")
    existentes = [linha[0] for linha in conexao.execute(f"SELECT codigo FROM {tabela}")]
    numeros = [int(c[len(prefixo):]) for c in existentes if re.fullmatch(rf"{prefixo}\d+", c)]
    return f"{prefixo}{max(numeros, default=0)+1:0{digitos}d}"


def verificar_uid_disponivel(conexao, uid: str | None, tipo: str, id_atual=None) -> None:
    if not uid:
        return
    for tabela in ("livros", "usuarios"):
        atual = consultar_um(conexao, f"SELECT id FROM {tabela} WHERE uid=?", (uid,))
        if atual and (tabela != tipo or int(atual["id"]) != int(id_atual or -1)):
            raise ErroNegocio("Este UID já está vinculado a outro cadastro.")


def criar_livro(conexao, dados: Mapping, id_atual=None) -> int:
    codigo = normalizar_codigo(dados.get("codigo"), "LIV")
    titulo = str(dados.get("titulo") or "").strip()
    autor = str(dados.get("autor") or "").strip()
    categoria = str(dados.get("categoria") or "Geral").strip() or "Geral"
    isbn = str(dados.get("isbn") or "").strip()
    localizacao = str(dados.get("localizacao") or "").strip()
    observacoes = str(dados.get("observacoes") or "").strip()
    uid = normalizar_uid(dados.get("uid"))
    if not 1 <= len(titulo) <= 180 or len(autor) > 120 or len(categoria) > 80 or len(isbn) > 30 or len(localizacao) > 80 or len(observacoes) > 500:
        raise ErroNegocio("Revise os campos do livro e seus tamanhos.")
    verificar_uid_disponivel(conexao, uid, "livros", id_atual)
    try:
        if id_atual is None:
            cursor = conexao.execute("""INSERT INTO livros(codigo,titulo,autor,categoria,isbn,uid,localizacao,observacoes)
                VALUES(?,?,?,?,?,?,?,?)""", (codigo,titulo,autor,categoria,isbn,uid,localizacao,observacoes))
            return int(cursor.lastrowid)
        cursor = conexao.execute("""UPDATE livros SET codigo=?,titulo=?,autor=?,categoria=?,isbn=?,uid=?,localizacao=?,observacoes=? WHERE id=?""",
            (codigo,titulo,autor,categoria,isbn,uid,localizacao,observacoes,id_atual))
        if not cursor.rowcount:
            raise ErroNegocio("Livro não encontrado.")
        return int(id_atual)
    except sqlite3.IntegrityError as erro:
        raise ErroNegocio("Código do livro ou UID já cadastrado.") from erro


def criar_usuario(conexao, dados: Mapping, id_atual=None) -> int:
    codigo = normalizar_codigo(dados.get("codigo"), "USR")
    nome = str(dados.get("nome") or "").strip()
    email = str(dados.get("email") or "").strip()
    uid = normalizar_uid(dados.get("uid"))
    ativo = 1 if str(dados.get("ativo", "1")) in ("1", "true", "True", "on") else 0
    if not 2 <= len(nome) <= 150 or len(email) > 180 or (email and ("@" not in email or email.startswith("@"))):
        raise ErroNegocio("Preencha um nome válido e revise o e-mail.")
    verificar_uid_disponivel(conexao, uid, "usuarios", id_atual)
    try:
        if id_atual is None:
            cursor = conexao.execute("INSERT INTO usuarios(codigo,nome,email,uid,ativo) VALUES(?,?,?,?,?)", (codigo,nome,email,uid,ativo))
            return int(cursor.lastrowid)
        cursor = conexao.execute("UPDATE usuarios SET codigo=?,nome=?,email=?,uid=?,ativo=? WHERE id=?", (codigo,nome,email,uid,ativo,id_atual))
        if not cursor.rowcount:
            raise ErroNegocio("Usuário não encontrado.")
        return int(id_atual)
    except sqlite3.IntegrityError as erro:
        raise ErroNegocio("Código do usuário ou UID já cadastrado.") from erro


def verificar_livro_disponivel(conexao, livro_id: int) -> None:
    if consultar_um(conexao, "SELECT 1 FROM emprestimos WHERE livro_id=? AND devolvido_em IS NULL", (livro_id,)):
        raise ErroNegocio("O livro já está emprestado.")
    if consultar_um(conexao, "SELECT 1 FROM reservas WHERE livro_id=? AND situacao='ativa'", (livro_id,)):
        raise ErroNegocio("O livro já está reservado.")


def iniciar_reserva(conexao, livro_id: int) -> str:
    """Inicia uma sessão exclusiva de confirmação: primeiro livro, depois cartão."""
    agora = int(time.time())
    conexao.execute("BEGIN IMMEDIATE")
    try:
        livro = consultar_um(conexao, "SELECT id,uid FROM livros WHERE id=?", (livro_id,))
        if not livro:
            raise ErroNegocio("Livro não encontrado.")
        if not livro["uid"]:
            raise ErroNegocio("Este livro ainda não tem uma etiqueta vinculada.")
        verificar_livro_disponivel(conexao, livro_id)
        conexao.execute("UPDATE sessoes_reserva SET estado='expirada' WHERE estado IN ('aguardando_livro','aguardando_usuario') AND expira_em<=?", (agora,))
        pendente = consultar_um(conexao, "SELECT id FROM sessoes_reserva WHERE estado IN ('aguardando_livro','aguardando_usuario')")
        if pendente:
            raise ErroNegocio("O leitor RFID está ocupado com outra reserva. Aguarde a conclusão.")
        captura = consultar_um(conexao, "SELECT id FROM capturas WHERE estado='aguardando' AND expira_em>?", (agora,))
        if captura:
            raise ErroNegocio("O leitor está capturando um UID para cadastro. Tente novamente depois.")
        identificador = secrets.token_urlsafe(24)
        conexao.execute("INSERT INTO sessoes_reserva(id,livro_id,estado,criada_em,expira_em,mensagem) VALUES(?,?,?,?,?,?)",
                         (identificador,livro_id,'aguardando_livro',agora,agora+PRAZO_LEITURA_RESERVA,
                          'Aproxime a etiqueta NFC do livro escolhido ao RC522.'))
        # A estação será dedicada à reserva enquanto a sessão estiver ativa.
        conexao.execute("UPDATE estado_operacao SET modo='consulta',usuario_pendente=NULL,usuario_expira_em=NULL WHERE id=1")
        conexao.commit()
        return identificador
    except Exception:
        conexao.rollback()
        raise


def consultar_reserva_pendente(conexao, identificador: str) -> dict:
    sessao = consultar_um(conexao, "SELECT * FROM sessoes_reserva WHERE id=?", (identificador,))
    if not sessao:
        raise ErroNegocio("Sessão de reserva não encontrada.")
    if sessao['estado'] in ('aguardando_livro','aguardando_usuario') and sessao['expira_em'] <= int(time.time()):
        conexao.execute("UPDATE sessoes_reserva SET estado='expirada', mensagem='Tempo encerrado. Inicie outra reserva.' WHERE id=?", (identificador,))
        sessao = consultar_um(conexao, "SELECT * FROM sessoes_reserva WHERE id=?", (identificador,))
    return {'estado': sessao['estado'], 'mensagem': sessao['mensagem'],
            'reserva_id': sessao['reserva_id'], 'tempo_restante': max(0,sessao['expira_em']-int(time.time()))}


def cancelar_reserva_pendente(conexao, identificador: str) -> None:
    conexao.execute("UPDATE sessoes_reserva SET estado='cancelada', mensagem='Operação cancelada.' "
                    "WHERE id=? AND estado IN ('aguardando_livro','aguardando_usuario')", (identificador,))


def cancelar_reserva(conexao, reserva_id: int) -> None:
    registro = consultar_um(conexao, "SELECT id FROM reservas WHERE id=? AND situacao='ativa'", (reserva_id,))
    if not registro:
        raise ErroNegocio("Reserva ativa não encontrada.")
    conexao.execute("UPDATE reservas SET situacao='cancelada', encerrada_em=datetime('now') WHERE id=?", (reserva_id,))


def processar_etapa_reserva(conexao, sessao, livro, usuario, agora: int, origem: str = "rfid") -> dict:
    """Valida a ordem livro→cartão; só grava a reserva após as duas leituras."""
    livro_registrado = consultar_um(conexao, 'SELECT codigo,titulo FROM livros WHERE id=?', (sessao['livro_id'],))
    if sessao['estado'] == 'aguardando_livro':
        if not livro or livro['id'] != sessao['livro_id']:
            return {'resultado':'erro','acao':'alerta','mensagem':'Aproxime a etiqueta do livro selecionado na tela.'}
        try:
            verificar_livro_disponivel(conexao, sessao['livro_id'])
        except ErroNegocio as erro:
            conexao.execute("UPDATE sessoes_reserva SET estado='cancelada',mensagem=? WHERE id=?", (str(erro),sessao['id']))
            return {'resultado':'erro','acao':'alerta','mensagem':str(erro)}
        mensagem = f"Livro {livro_registrado['codigo']} identificado. Agora aproxime seu cartão USR."
        conexao.execute("UPDATE sessoes_reserva SET estado='aguardando_usuario',mensagem=? WHERE id=?", (mensagem,sessao['id']))
        return {'resultado':'aguardando','acao':'aguardando','mensagem':mensagem}
    if not usuario:
        return {'resultado':'erro','acao':'alerta','mensagem':'Livro confirmado. Agora aproxime um cartão de usuário válido.'}
    if not usuario['ativo']:
        return {'resultado':'erro','acao':'alerta','mensagem':'Este cartão pertence a um usuário inativo.'}
    try:
        verificar_livro_disponivel(conexao, sessao['livro_id'])
        cursor = conexao.execute("INSERT INTO reservas(livro_id,usuario_id,situacao,origem) VALUES(?,?,'ativa',?)",
                                 (sessao['livro_id'],usuario['id'],origem))
        mensagem = f"Reserva confirmada: {livro_registrado['titulo']} para {usuario['codigo']}."
        conexao.execute("UPDATE sessoes_reserva SET estado='concluida',reserva_id=?,mensagem=? WHERE id=?",
                        (cursor.lastrowid,mensagem,sessao['id']))
        return {'resultado':'ok','acao':'confirmacao','mensagem':mensagem}
    except (ErroNegocio,sqlite3.IntegrityError):
        mensagem = 'O livro deixou de estar disponível. Tente outra reserva.'
        conexao.execute("UPDATE sessoes_reserva SET estado='cancelada',mensagem=? WHERE id=?", (mensagem,sessao['id']))
        return {'resultado':'erro','acao':'alerta','mensagem':mensagem}


def emprestar_livro(conexao, livro_id: int, usuario_id: int, origem="painel") -> int:
    livro = consultar_um(conexao, "SELECT * FROM livros WHERE id=?", (livro_id,))
    usuario = consultar_um(conexao, "SELECT * FROM usuarios WHERE id=?", (usuario_id,))
    if not livro or not usuario:
        raise ErroNegocio("Livro ou usuário não encontrado.")
    if not usuario["ativo"]:
        raise ErroNegocio("O usuário está inativo.")
    if consultar_um(conexao, "SELECT 1 FROM emprestimos WHERE livro_id=? AND devolvido_em IS NULL", (livro_id,)):
        raise ErroNegocio("Este livro já está emprestado.")
    reserva = consultar_um(conexao, "SELECT id,usuario_id FROM reservas WHERE livro_id=? AND situacao='ativa'", (livro_id,))
    if reserva and reserva['usuario_id'] != usuario_id:
        raise ErroNegocio("Livro reservado para outro usuário.")
    if reserva:
        conexao.execute("UPDATE reservas SET situacao='retirada',encerrada_em=datetime('now') WHERE id=?", (reserva['id'],))
    cursor = conexao.execute("INSERT INTO emprestimos(livro_id,usuario_id,origem) VALUES(?,?,?)", (livro_id,usuario_id,origem))
    return int(cursor.lastrowid)


def devolver_livro(conexao, livro_id: int, usuario_id: int | None = None) -> int:
    atual = consultar_um(conexao, "SELECT * FROM emprestimos WHERE livro_id=? AND devolvido_em IS NULL", (livro_id,))
    if not atual:
        raise ErroNegocio("Este livro não possui empréstimo ativo.")
    if usuario_id is not None and int(atual["usuario_id"]) != int(usuario_id):
        raise ErroNegocio("Este livro está emprestado a outro usuário.")
    conexao.execute("UPDATE emprestimos SET devolvido_em=datetime('now') WHERE id=?", (atual["id"],))
    return int(atual["id"])


def mudar_modo(conexao, modo: str) -> None:
    if modo not in ("consulta", "emprestimo", "devolucao"):
        raise ErroNegocio("Modo inválido.")
    conexao.execute("UPDATE estado_operacao SET modo=?,usuario_pendente=NULL,usuario_expira_em=NULL,atualizado_em=datetime('now') WHERE id=1", (modo,))


def iniciar_captura(conexao, tipo: str) -> str:
    if tipo not in ("livro", "usuario"):
        raise ErroNegocio("Tipo de cadastro inválido.")
    pendente = consultar_um(conexao, "SELECT id FROM sessoes_reserva WHERE estado IN ('aguardando_livro','aguardando_usuario') AND expira_em>?", (int(time.time()),))
    if pendente:
        raise ErroNegocio("Há uma reserva em andamento no RC522. Aguarde sua conclusão.")
    identificador = secrets.token_urlsafe(18)
    agora = int(time.time())
    conexao.execute("UPDATE capturas SET estado='cancelado' WHERE estado='aguardando'")
    conexao.execute("INSERT INTO capturas(id,tipo,criada_em,expira_em) VALUES(?,?,?,?)", (identificador,tipo,agora,agora+75))
    return identificador


def consultar_captura(conexao, identificador: str) -> dict:
    captura = consultar_um(conexao, "SELECT * FROM capturas WHERE id=?", (identificador,))
    if not captura:
        raise ErroNegocio("Leitura não encontrada.")
    estado = captura["estado"]
    if estado == "aguardando" and captura["expira_em"] < int(time.time()):
        conexao.execute("UPDATE capturas SET estado='expirado' WHERE id=?", (identificador,))
        estado = "expirado"
    return {"estado": estado, "uid": captura["uid"], "tipo": captura["tipo"]}


def identificar_uid(conexao, uid: str):
    livro = consultar_um(conexao, "SELECT id,codigo,titulo FROM livros WHERE uid=?", (uid,))
    usuario = consultar_um(conexao, "SELECT id,codigo,nome,ativo FROM usuarios WHERE uid=?", (uid,))
    return livro, usuario


def processar_leitura(conexao, uid_bruto: str, evento_id: str = "", dispositivo: str = "esp32-principal") -> dict:
    uid = normalizar_uid(uid_bruto, obrigatorio=True)
    evento_id = str(evento_id or "").strip()[:100] or None
    dispositivo = str(dispositivo or "esp32-principal").strip()[:80]
    if not dispositivo:
        dispositivo = "esp32-principal"

    # Escrita serializada para evitar empréstimos simultâneos do mesmo exemplar.
    conexao.execute("BEGIN IMMEDIATE")
    try:
        if evento_id:
            repetido = consultar_um(conexao, "SELECT resposta_json FROM leituras WHERE evento_id=?", (evento_id,))
            if repetido:
                conexao.commit()
                return json.loads(repetido["resposta_json"])
        agora = int(time.time())
        conexao.execute("INSERT INTO dispositivos(identificador) VALUES(?) ON CONFLICT(identificador) DO UPDATE SET ultima_atividade=datetime('now')", (dispositivo,))
        captura = consultar_um(conexao, "SELECT * FROM capturas WHERE estado='aguardando' ORDER BY criada_em DESC LIMIT 1")
        if captura and captura["expira_em"] < agora:
            conexao.execute("UPDATE capturas SET estado='expirado' WHERE id=?", (captura["id"],))
            captura = None

        sessao_reserva = consultar_um(conexao, "SELECT * FROM sessoes_reserva WHERE estado IN ('aguardando_livro','aguardando_usuario') ORDER BY criada_em DESC LIMIT 1")
        if sessao_reserva and sessao_reserva['expira_em'] <= agora:
            conexao.execute("UPDATE sessoes_reserva SET estado='expirada',mensagem='Tempo encerrado.' WHERE id=?",(sessao_reserva['id'],))
            sessao_reserva = None
        if captura:
            conexao.execute("UPDATE capturas SET uid=?, estado='capturado' WHERE id=?", (uid,captura["id"]))
            resposta = {"resultado": "capturado", "acao": "confirmacao", "mensagem": "UID capturado para cadastro.", "uid": uid}
        elif sessao_reserva:
            livro, usuario = identificar_uid(conexao, uid)
            origem_reserva = "simulacao" if dispositivo == "simulador-painel" else "rfid"
            resposta = processar_etapa_reserva(
                conexao, sessao_reserva, livro, usuario, agora, origem_reserva
            )
        else:
            livro, usuario = identificar_uid(conexao, uid)
            estado = consultar_um(conexao, "SELECT * FROM estado_operacao WHERE id=1")
            modo = estado["modo"]
            if modo == "consulta":
                descricao = f"Livro: {livro['titulo']}" if livro else f"Usuário: {usuario['nome']}" if usuario else "UID não cadastrado."
                resposta = {"resultado": "consulta", "acao": "consulta", "mensagem": descricao, "uid": uid}
            elif usuario:
                if not usuario["ativo"]:
                    resposta = {"resultado": "erro", "acao": "alerta", "mensagem": "Cartão de usuário inativo."}
                else:
                    conexao.execute("UPDATE estado_operacao SET usuario_pendente=?,usuario_expira_em=? WHERE id=1", (usuario["id"],agora+120))
                    resposta = {"resultado": "aguardando", "acao": "aguardando", "mensagem": f"{usuario['nome']} identificado. Aproxime o livro."}
            elif livro:
                if not estado["usuario_pendente"] or not estado["usuario_expira_em"] or estado["usuario_expira_em"] < agora:
                    conexao.execute("UPDATE estado_operacao SET usuario_pendente=NULL,usuario_expira_em=NULL WHERE id=1")
                    resposta = {"resultado": "erro", "acao": "alerta", "mensagem": "Aproxime o cartão de usuário antes do livro."}
                else:
                    try:
                        if modo == "emprestimo":
                            emprestar_livro(conexao, livro["id"], estado["usuario_pendente"], "rfid")
                            mensagem = f"Empréstimo registrado: {livro['titulo']}."
                        else:
                            devolver_livro(conexao, livro["id"], estado["usuario_pendente"])
                            mensagem = f"Devolução registrada: {livro['titulo']}."
                        resposta = {"resultado": "ok", "acao": "confirmacao", "mensagem": mensagem}
                    except ErroNegocio as erro:
                        resposta = {"resultado": "erro", "acao": "alerta", "mensagem": str(erro)}
                    conexao.execute("UPDATE estado_operacao SET usuario_pendente=NULL,usuario_expira_em=NULL WHERE id=1")
            else:
                resposta = {"resultado": "erro", "acao": "alerta", "mensagem": "UID não cadastrado. Faça o cadastro primeiro."}

        conexao.execute("INSERT INTO leituras(evento_id,dispositivo,uid,resultado,mensagem,resposta_json) VALUES(?,?,?,?,?,?)",
            (evento_id,dispositivo,uid,resposta["resultado"],resposta["mensagem"],json.dumps(resposta,ensure_ascii=False)))
        conexao.commit()
        return resposta
    except Exception:
        conexao.rollback()
        raise


def informar_telemetria(conexao, dispositivo: str, distancia: float | None) -> None:
    identificador = str(dispositivo or "esp32-principal").strip()[:80]
    if distancia is not None and not (0 <= distancia <= 500):
        raise ErroNegocio("Distância fora da faixa esperada.")
    conexao.execute("""INSERT INTO dispositivos(identificador,distancia_cm) VALUES(?,?)
        ON CONFLICT(identificador) DO UPDATE SET ultima_atividade=datetime('now'),distancia_cm=excluded.distancia_cm""", (identificador,distancia))
