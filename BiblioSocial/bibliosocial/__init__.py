"""Aplicação Flask do BiblioSocial IoT (backend + painel + API da ESP32)."""
from __future__ import annotations

import csv
import hmac
import io
import os
import secrets
import sqlite3
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from .banco import fechar_banco, inicializar_banco, obter_banco
from .comandos import (
    ErroComando, confirmar_comando, consultar_comando, criar_comando,
    definir_sinalizacao_simulacao, entregar_proximo_comando, expirar_comandos,
    normalizar_dispositivo, sinalizacao_simulacao_ativa,
)
from .servicos import (
    ErroNegocio, consultar_captura, criar_livro, criar_usuario, devolver_livro,
    emprestar_livro, informar_telemetria, iniciar_captura, mudar_modo,
    proximo_codigo, processar_leitura, iniciar_reserva, consultar_reserva_pendente,
    cancelar_reserva_pendente, cancelar_reserva,
)

RAIZ = Path(__file__).resolve().parent.parent


def _inteiro_ambiente(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao


def criar_aplicacao(configuracao: dict | None = None) -> Flask:
    load_dotenv(RAIZ / '.env')
    aplicacao = Flask(__name__, template_folder='templates', static_folder='static')
    caminho = os.getenv('CAMINHO_BANCO', 'dados/bibliosocial.db')
    aplicacao.config.update(
        SECRET_KEY=os.getenv('CHAVE_SESSAO', ''),
        CHAVE_DISPOSITIVO=os.getenv('CHAVE_DISPOSITIVO', ''),
        DISPOSITIVO_PADRAO=os.getenv('DISPOSITIVO_PADRAO', 'esp32-principal').strip() or 'esp32-principal',
        PRAZO_COMANDO_SEGUNDOS=_inteiro_ambiente('PRAZO_COMANDO_SEGUNDOS', 45),
        CAMINHO_BANCO=str((RAIZ / caminho).resolve()) if not Path(caminho).is_absolute() else caminho,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SEGURO', 'false').lower() == 'true',
        MAX_CONTENT_LENGTH=128 * 1024,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    )
    if configuracao:
        aplicacao.config.update(configuracao)
    aplicacao.config['DISPOSITIVO_PADRAO'] = normalizar_dispositivo(
        aplicacao.config['DISPOSITIVO_PADRAO']
    )
    aplicacao.config['PRAZO_COMANDO_SEGUNDOS'] = max(
        10, min(int(aplicacao.config['PRAZO_COMANDO_SEGUNDOS']), 300)
    )
    if not aplicacao.config.get('TESTING'):
        for chave in ('SECRET_KEY', 'CHAVE_DISPOSITIVO'):
            texto = str(aplicacao.config.get(chave, ''))
            if len(texto) < 24 or texto.upper().startswith('GERE_'):
                raise RuntimeError('Execute primeiro: python configurar.py (faltando ' + chave + ')')
    inicializar_banco(aplicacao.config['CAMINHO_BANCO'])
    aplicacao.teardown_appcontext(fechar_banco)

    def exigir_login(funcao):
        @wraps(funcao)
        def interna(*argumentos, **opcoes):
            if not session.get('administrador'):
                if request.path.startswith('/api/'):
                    return jsonify(erro='Faça login para continuar.'), 401
                return redirect(url_for('login'))
            return funcao(*argumentos, **opcoes)
        return interna

    def exigir_dispositivo(funcao):
        @wraps(funcao)
        def interna(*argumentos, **opcoes):
            recebido = request.headers.get('X-Chave-Dispositivo', request.headers.get('X-API-Key', ''))
            esperado = aplicacao.config['CHAVE_DISPOSITIVO']
            if not esperado or not hmac.compare_digest(str(recebido), str(esperado)):
                return jsonify(erro='Chave da ESP32 inválida.'), 401
            return funcao(*argumentos, **opcoes)
        return interna

    def resposta_comando(comando: dict) -> dict:
        campos = (
            'id', 'dispositivo', 'tipo', 'estado', 'origem', 'criado_em',
            'expira_em', 'entregue_em', 'concluido_em', 'detalhe',
        )
        return {campo: comando.get(campo) for campo in campos}

    @aplicacao.before_request
    def verificar_csrf():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        if request.path.startswith('/api/dispositivo/'):
            return None  # As rotas ESP32 possuem autenticação exclusiva por chave.
        esperado = session.get('csrf_token', '')
        recebido = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
        if not esperado or not hmac.compare_digest(str(esperado), str(recebido)):
            if request.path.startswith('/api/'):
                return jsonify(erro='Token de sessão inválido. Atualize a página.'), 400
            abort(400, 'Token de segurança inválido. Atualize a página.')
        return None

    @aplicacao.context_processor
    def variaveis_globais():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return {'csrf_token': session['csrf_token'], 'ano_atual': datetime.now().year}

    @aplicacao.get('/saude')
    def saude():
        return jsonify(servico='BiblioSocial IoT', status='ativo')

    @aplicacao.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            if session.get('administrador'):
                return redirect(url_for('painel'))
            banco = obter_banco()
            configurado = banco.execute('SELECT 1 FROM administradores LIMIT 1').fetchone() is not None
            return render_template('login.html', configurado=configurado)
        usuario = request.form.get('usuario', '').strip()
        senha = request.form.get('senha', '')
        banco = obter_banco()
        adm = banco.execute('SELECT * FROM administradores WHERE usuario=?', (usuario,)).fetchone()
        if adm is None or not check_password_hash(adm['senha_hash'], senha):
            flash('Usuário ou senha incorretos.', 'erro')
            return redirect(url_for('login'))
        session.clear()
        session.permanent = True
        session['administrador'] = adm['usuario']
        session['csrf_token'] = secrets.token_urlsafe(32)
        flash('Login realizado.', 'sucesso')
        return redirect(url_for('painel'))

    @aplicacao.post('/sair')
    @exigir_login
    def sair():
        session.clear()
        return redirect(url_for('login'))

    def consultar_catalogo(banco):
        return banco.execute("""SELECT l.id,l.codigo,l.titulo,l.autor,l.categoria,l.uid,
              CASE WHEN EXISTS(SELECT 1 FROM emprestimos e WHERE e.livro_id=l.id AND e.devolvido_em IS NULL)
                   THEN 'emprestado'
                   WHEN EXISTS(SELECT 1 FROM reservas r WHERE r.livro_id=l.id AND r.situacao='ativa')
                   THEN 'reservado' ELSE 'disponivel' END AS situacao
              FROM livros l ORDER BY l.codigo""").fetchall()

    @aplicacao.get('/catalogo')
    def catalogo_publico():
        return render_template('catalogo.html', catalogo=consultar_catalogo(obter_banco()))

    @aplicacao.get('/')
    @exigir_login
    def painel():
        banco = obter_banco()
        indicadores = {
            'livros': banco.execute('SELECT COUNT(*) FROM livros').fetchone()[0],
            'emprestados': banco.execute('SELECT COUNT(*) FROM emprestimos WHERE devolvido_em IS NULL').fetchone()[0],
            'reservados': banco.execute("SELECT COUNT(*) FROM reservas WHERE situacao='ativa'").fetchone()[0],
        }
        indicadores['disponiveis'] = indicadores['livros'] - indicadores['emprestados'] - indicadores['reservados']
        indicadores['indisponiveis'] = indicadores['emprestados'] + indicadores['reservados']
        catalogo = consultar_catalogo(banco)
        return render_template('painel.html', indicadores=indicadores, catalogo=catalogo)

    @aplicacao.get('/livros')
    @exigir_login
    def livros():
        banco = obter_banco()
        busca = request.args.get('busca', '').strip()[:100]
        categoria = request.args.get('categoria', '').strip()[:80]
        situacao = request.args.get('situacao', '').strip().lower()
        comando = '''SELECT l.*,CASE WHEN e.id IS NOT NULL THEN 'emprestado' WHEN r.id IS NOT NULL THEN 'reservado' ELSE 'disponivel' END AS situacao
            FROM livros l LEFT JOIN emprestimos e ON e.livro_id=l.id AND e.devolvido_em IS NULL LEFT JOIN reservas r ON r.livro_id=l.id AND r.situacao='ativa' WHERE 1=1'''
        filtros = []
        if busca:
            comando += ' AND (l.codigo LIKE ? OR l.titulo LIKE ? OR l.autor LIKE ? OR l.uid LIKE ?)'
            filtros.extend(['%' + busca + '%'] * 4)
        if categoria:
            comando += ' AND l.categoria=?'
            filtros.append(categoria)
        if situacao in ('disponivel', 'emprestado', 'reservado'):
            comando += {'disponivel':' AND e.id IS NULL AND r.id IS NULL', 'emprestado':' AND e.id IS NOT NULL', 'reservado':' AND r.id IS NOT NULL'}[situacao]
        comando += ' ORDER BY l.codigo'
        registros = banco.execute(comando, filtros).fetchall()
        categorias = banco.execute('SELECT DISTINCT categoria FROM livros ORDER BY categoria').fetchall()
        return render_template('livros.html', livros=registros, categorias=categorias, busca=busca,
                               categoria_atual=categoria, situacao_atual=situacao)

    @aplicacao.route('/livros/novo', methods=['GET', 'POST'])
    @aplicacao.route('/livros/<int:identificador>/editar', methods=['GET', 'POST'])
    @exigir_login
    def formulario_livro(identificador=None):
        banco = obter_banco()
        livro = banco.execute('SELECT * FROM livros WHERE id=?', (identificador,)).fetchone() if identificador else None
        if identificador and not livro:
            abort(404)
        if request.method == 'POST':
            try:
                with banco:
                    criar_livro(banco, request.form, id_atual=identificador)
                flash('Livro salvo com sucesso.', 'sucesso')
                return redirect(url_for('livros'))
            except ErroNegocio as erro:
                flash(str(erro), 'erro')
            except sqlite3.IntegrityError:
                flash('UID ou código já utilizado por outro cadastro.', 'erro')
        codigo_sugerido = proximo_codigo(banco, 'livros', 'LIV', 3)
        return render_template('formulario_livro.html', livro=livro, codigo_sugerido=codigo_sugerido)

    @aplicacao.post('/livros/<int:identificador>/excluir')
    @exigir_login
    def excluir_livro(identificador):
        banco = obter_banco()
        try:
            with banco:
                banco.execute('DELETE FROM livros WHERE id=?', (identificador,))
            flash('Livro excluído.', 'sucesso')
        except sqlite3.IntegrityError:
            flash('Não é possível excluir um livro com histórico de empréstimos.', 'erro')
        return redirect(url_for('livros'))

    @aplicacao.get('/usuarios')
    @exigir_login
    def usuarios():
        banco = obter_banco()
        busca = request.args.get('busca', '').strip()[:100]
        if busca:
            registros = banco.execute('''SELECT * FROM usuarios WHERE nome LIKE ? OR codigo LIKE ? OR uid LIKE ? ORDER BY codigo''',
                                     ('%' + busca + '%',) * 3).fetchall()
        else:
            registros = banco.execute('SELECT * FROM usuarios ORDER BY codigo').fetchall()
        return render_template('usuarios.html', usuarios=registros, busca=busca)

    @aplicacao.route('/usuarios/novo', methods=['GET', 'POST'])
    @aplicacao.route('/usuarios/<int:identificador>/editar', methods=['GET', 'POST'])
    @exigir_login
    def formulario_usuario(identificador=None):
        banco = obter_banco()
        usuario = banco.execute('SELECT * FROM usuarios WHERE id=?', (identificador,)).fetchone() if identificador else None
        if identificador and not usuario:
            abort(404)
        if request.method == 'POST':
            try:
                with banco:
                    criar_usuario(banco, request.form, id_atual=identificador)
                flash('Usuário salvo com sucesso.', 'sucesso')
                return redirect(url_for('usuarios'))
            except ErroNegocio as erro:
                flash(str(erro), 'erro')
            except sqlite3.IntegrityError:
                flash('Código ou UID já registrado.', 'erro')
        codigo_sugerido = proximo_codigo(banco, 'usuarios', 'USR', 2)
        return render_template('formulario_usuario.html', usuario=usuario, codigo_sugerido=codigo_sugerido)

    @aplicacao.post('/usuarios/<int:identificador>/excluir')
    @exigir_login
    def excluir_usuario(identificador):
        banco = obter_banco()
        try:
            with banco:
                banco.execute('DELETE FROM usuarios WHERE id=?', (identificador,))
            flash('Usuário excluído.', 'sucesso')
        except sqlite3.IntegrityError:
            flash('Não é possível excluir usuário com histórico de empréstimos.', 'erro')
        return redirect(url_for('usuarios'))

    @aplicacao.get('/emprestimos')
    @exigir_login
    def emprestimos():
        banco = obter_banco()
        historico = banco.execute('''SELECT e.id,e.emprestado_em,e.devolvido_em,e.origem,l.titulo,
                l.codigo AS codigo_livro,u.nome,u.codigo AS codigo_usuario
            FROM emprestimos e JOIN livros l ON l.id=e.livro_id
            JOIN usuarios u ON u.id=e.usuario_id ORDER BY e.id DESC LIMIT 300''').fetchall()
        disponiveis = banco.execute('''SELECT id,codigo,titulo FROM livros WHERE id NOT IN
            (SELECT livro_id FROM emprestimos WHERE devolvido_em IS NULL)
            AND id NOT IN (SELECT livro_id FROM reservas WHERE situacao='ativa') ORDER BY codigo''').fetchall()
        ativos = banco.execute('SELECT id,codigo,nome FROM usuarios WHERE ativo=1 ORDER BY codigo').fetchall()
        return render_template('emprestimos.html', emprestimos=historico, livros=disponiveis, usuarios=ativos)

    @aplicacao.post('/emprestimos/registrar')
    @exigir_login
    def registrar_emprestimo():
        try:
            livro_id = int(request.form.get('livro_id', ''))
            usuario_id = int(request.form.get('usuario_id', ''))
            banco = obter_banco()
            with banco:
                emprestar_livro(banco, livro_id, usuario_id)
            flash('Empréstimo registrado com sucesso.', 'sucesso')
        except (ErroNegocio, ValueError, sqlite3.IntegrityError) as erro:
            flash(str(erro) or 'Não foi possível registrar o empréstimo.', 'erro')
        return redirect(url_for('emprestimos'))

    @aplicacao.post('/emprestimos/<int:identificador>/devolver')
    @exigir_login
    def registrar_devolucao(identificador):
        banco = obter_banco()
        atual = banco.execute('SELECT livro_id FROM emprestimos WHERE id=? AND devolvido_em IS NULL', (identificador,)).fetchone()
        if not atual:
            flash('Empréstimo não encontrado ou já devolvido.', 'erro')
        else:
            with banco:
                devolver_livro(banco, atual['livro_id'])
            flash('Devolução registrada com sucesso.', 'sucesso')
        return redirect(url_for('emprestimos'))

    @aplicacao.get('/reservas')
    @exigir_login
    def reservas():
        banco = obter_banco()
        registros = banco.execute("""SELECT r.*, l.codigo AS codigo_livro,l.titulo,u.nome,u.codigo AS codigo_usuario
                     FROM reservas r JOIN livros l ON l.id=r.livro_id JOIN usuarios u ON u.id=r.usuario_id
                     ORDER BY r.id DESC LIMIT 250""").fetchall()
        return render_template('reservas.html', reservas=registros)

    @aplicacao.post('/reservas/<int:identificador>/cancelar')
    @exigir_login
    def cancelar_reserva_admin(identificador):
        banco = obter_banco()
        try:
            with banco:
                cancelar_reserva(banco, identificador)
            flash('Reserva cancelada. O livro voltou a ficar disponível.', 'sucesso')
        except ErroNegocio as erro:
            flash(str(erro), 'erro')
        return redirect(url_for('reservas'))

    @aplicacao.post('/reservas/<int:identificador>/emprestar')
    @exigir_login
    def retirar_reserva_admin(identificador):
        banco = obter_banco()
        registro = banco.execute("SELECT livro_id,usuario_id FROM reservas WHERE id=? AND situacao='ativa'", (identificador,)).fetchone()
        try:
            if not registro:
                raise ErroNegocio('Reserva ativa não encontrada.')
            with banco:
                emprestar_livro(banco, registro['livro_id'], registro['usuario_id'], 'painel')
            flash('Retirada confirmada: reserva convertida em empréstimo.', 'sucesso')
        except (ErroNegocio,sqlite3.IntegrityError) as erro:
            flash(str(erro), 'erro')
        return redirect(url_for('reservas'))

    @aplicacao.post('/api/reservas/iniciar')
    def iniciar_reserva_http():
        dados = request.get_json(silent=True) or {}
        try:
            livro_id = int(dados.get('livro_id'))
            banco = obter_banco()
            identificador = iniciar_reserva(banco, livro_id)
            session['reserva_pendente'] = identificador
            return jsonify(identificador=identificador, prazo_segundos=150,
                           mensagem='Aproxime a etiqueta NFC do livro no RC522.')
        except (ErroNegocio,TypeError,ValueError) as erro:
            return jsonify(erro=str(erro) or 'Livro inválido.'), 409

    @aplicacao.get('/api/reservas/<identificador>')
    def consultar_reserva_http(identificador):
        if identificador != session.get('reserva_pendente'):
            return jsonify(erro='Esta reserva não pertence à sua sessão.'), 403
        banco = obter_banco()
        try:
            with banco:
                estado = consultar_reserva_pendente(banco, identificador)
            return jsonify(estado)
        except ErroNegocio as erro:
            return jsonify(erro=str(erro)), 404

    @aplicacao.post('/api/reservas/<identificador>/cancelar')
    def cancelar_reserva_http(identificador):
        if identificador != session.get('reserva_pendente'):
            return jsonify(erro='Esta reserva não pertence à sua sessão.'), 403
        banco = obter_banco()
        with banco:
            cancelar_reserva_pendente(banco,identificador)
        return jsonify(estado='cancelada')

    @aplicacao.get('/leituras')
    @exigir_login
    def leituras():
        banco = obter_banco()
        registros = banco.execute('SELECT * FROM leituras ORDER BY id DESC LIMIT 300').fetchall()
        return render_template('leituras.html', leituras=registros)

    @aplicacao.route('/operacao', methods=['GET', 'POST'])
    @exigir_login
    def operacao():
        banco = obter_banco()
        if request.method == 'POST':
            try:
                with banco:
                    mudar_modo(banco, request.form.get('modo', 'consulta'))
                flash('Modo de operação atualizado.', 'sucesso')
            except ErroNegocio as erro:
                flash(str(erro), 'erro')
            return redirect(url_for('operacao'))
        estado = banco.execute('SELECT * FROM estado_operacao WHERE id=1').fetchone()
        with banco:
            expirar_comandos(banco)
        dispositivos = banco.execute('SELECT * FROM dispositivos ORDER BY ultima_atividade DESC').fetchall()
        comandos = banco.execute(
            'SELECT * FROM comandos_dispositivo ORDER BY criado_em DESC,id DESC LIMIT 12'
        ).fetchall()
        ultima = banco.execute('SELECT * FROM leituras ORDER BY id DESC LIMIT 1').fetchone()
        return render_template(
            'operacao.html', estado=estado, dispositivos=dispositivos,
            ultima=ultima, comandos=comandos,
            dispositivo_padrao=aplicacao.config['DISPOSITIVO_PADRAO'],
        )

    @aplicacao.get('/simulador')
    @exigir_login
    def simulador():
        banco = obter_banco()
        registros = banco.execute('''SELECT codigo,titulo AS nome,uid,'livro' AS tipo FROM livros WHERE uid IS NOT NULL
            UNION ALL SELECT codigo,nome,uid,'usuario' FROM usuarios WHERE uid IS NOT NULL ORDER BY tipo,codigo''').fetchall()
        return render_template(
            'simulador.html', registros=registros,
            sinalizacao_fisica=sinalizacao_simulacao_ativa(banco),
        )

    @aplicacao.post('/api/simulacao/leitura')
    @exigir_login
    def simular_leitura():
        dados = request.get_json(silent=True) or {}
        banco = obter_banco()
        sinalizacao_ativa = sinalizacao_simulacao_ativa(banco)

        def enfileirar_sinal(tipo: str) -> dict | None:
            if not sinalizacao_ativa:
                return None
            try:
                with banco:
                    return criar_comando(
                        banco, tipo, aplicacao.config['DISPOSITIVO_PADRAO'],
                        origem='simulacao', criado_por=session.get('administrador'),
                        prazo_segundos=aplicacao.config['PRAZO_COMANDO_SEGUNDOS'],
                    )
            except (ErroComando, sqlite3.Error):
                aplicacao.logger.exception('Não foi possível enfileirar o sinal da simulação.')
                return {'id': None, 'estado': 'falhou'}

        try:
            resposta = processar_leitura(banco, dados.get('uid'),
                                        evento_id='sim-' + secrets.token_hex(10), dispositivo='simulador-painel')
            comando = None
            if resposta.get('resultado') == 'ok':
                comando = enfileirar_sinal('sinal_sucesso')
            elif resposta.get('resultado') == 'erro':
                comando = enfileirar_sinal('sinal_erro')
            resposta['simulacao'] = True
            resposta['sinalizacao_fisica'] = (
                comando['estado'] if comando else
                ('nao_aplicavel' if sinalizacao_ativa else 'desativada')
            )
            if comando:
                resposta['comando_id'] = comando['id']
            if resposta.get('resultado') == 'ok' and resposta.get('mensagem', '').startswith('Reserva confirmada:'):
                resposta['reserva_simulada'] = True
                resposta['mensagem'] = resposta['mensagem'].replace(
                    'Reserva confirmada:', 'Reserva realizada por simulação:', 1
                )
            return jsonify(resposta)
        except ErroNegocio as erro:
            comando = enfileirar_sinal('sinal_erro')
            return jsonify(
                erro=str(erro), simulacao=True,
                sinalizacao_fisica=(
                    comando['estado'] if comando else
                    ('nao_aplicavel' if sinalizacao_ativa else 'desativada')
                ),
                comando_id=comando['id'] if comando else None,
            ), 400

    @aplicacao.post('/api/configuracoes/sinalizacao-simulacao')
    @exigir_login
    def configurar_sinalizacao_simulacao():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict) or not isinstance(dados.get('ativa'), bool):
            return jsonify(erro='Informe o campo booleano "ativa".'), 400
        banco = obter_banco()
        with banco:
            definir_sinalizacao_simulacao(banco, dados['ativa'])
        return jsonify(ativa=dados['ativa'])

    @aplicacao.post('/api/comandos')
    @exigir_login
    def criar_comando_painel():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify(erro='Envie objeto JSON.'), 400
        try:
            banco = obter_banco()
            with banco:
                comando = criar_comando(
                    banco, dados.get('tipo'), aplicacao.config['DISPOSITIVO_PADRAO'],
                    origem='painel', criado_por=session.get('administrador'),
                    prazo_segundos=aplicacao.config['PRAZO_COMANDO_SEGUNDOS'],
                )
            return jsonify(comando=resposta_comando(comando)), 202
        except (ErroComando, TypeError, ValueError) as erro:
            return jsonify(erro=str(erro)), 400

    @aplicacao.get('/api/comandos/<identificador>')
    @exigir_login
    def status_comando_painel(identificador):
        banco = obter_banco()
        with banco:
            comando = consultar_comando(banco, identificador)
        if comando is None:
            return jsonify(erro='Comando não encontrado.'), 404
        return jsonify(comando=resposta_comando(comando))

    @aplicacao.post('/api/captura/iniciar')
    @exigir_login
    def iniciar_captura_uid():
        dados = request.get_json(silent=True) or {}
        banco = obter_banco()
        try:
            with banco:
                identificador = iniciar_captura(banco, dados.get('tipo', ''))
            session['captura_ativa'] = identificador
            return jsonify(identificador=identificador, prazo_segundos=75)
        except ErroNegocio as erro:
            return jsonify(erro=str(erro)), 400

    @aplicacao.get('/api/captura/<identificador>')
    @exigir_login
    def status_captura(identificador):
        if identificador != session.get('captura_ativa'):
            return jsonify(erro='Captura não pertence a esta sessão.'), 403
        banco = obter_banco()
        try:
            with banco:
                estado = consultar_captura(banco, identificador)
            return jsonify(estado)
        except ErroNegocio as erro:
            return jsonify(erro=str(erro)), 404

    @aplicacao.get('/api/painel/estado')
    @exigir_login
    def estado_painel():
        banco = obter_banco()
        estado = banco.execute('SELECT modo,usuario_pendente FROM estado_operacao WHERE id=1').fetchone()
        eventos = banco.execute('SELECT uid,mensagem,resultado,criada_em FROM leituras ORDER BY id DESC LIMIT 5').fetchall()
        dispositivos = banco.execute('SELECT identificador,ultima_atividade,distancia_cm FROM dispositivos ORDER BY ultima_atividade DESC').fetchall()
        return jsonify(operacao=dict(estado), leituras=[dict(e) for e in eventos],
                       dispositivos=[dict(d) for d in dispositivos])

    @aplicacao.post('/api/dispositivo/leitura')
    @exigir_dispositivo
    def leitura_dispositivo():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify(erro='Envie objeto JSON.'), 400
        try:
            resposta = processar_leitura(obter_banco(), dados.get('uid'),
                                        evento_id=dados.get('evento_id', ''),
                                        dispositivo=dados.get('dispositivo', 'esp32-principal'))
            return jsonify(resposta)
        except ErroNegocio as erro:
            return jsonify(erro=str(erro)), 400
        except sqlite3.IntegrityError:
            return jsonify(erro='Conflito no cadastro ou operação.'), 409

    @aplicacao.post('/api/dispositivo/telemetria')
    @exigir_dispositivo
    def telemetria_dispositivo():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify(erro='Envie objeto JSON.'), 400
        try:
            valor = dados.get('distancia_cm')
            distancia = float(valor) if valor is not None else None
            banco = obter_banco()
            with banco:
                informar_telemetria(banco, dados.get('dispositivo', 'esp32-principal'), distancia)
            return jsonify(resultado='ok', mensagem='Telemetria registrada.')
        except (TypeError, ValueError, ErroNegocio) as erro:
            return jsonify(erro=str(erro)), 400

    @aplicacao.get('/api/dispositivo/estado')
    @exigir_dispositivo
    def estado_dispositivo():
        banco = obter_banco()
        estado = banco.execute('SELECT modo FROM estado_operacao WHERE id=1').fetchone()
        return jsonify(modo=estado['modo'], nome='BiblioSocial IoT')

    @aplicacao.get('/api/dispositivo/comandos/proximo')
    @exigir_dispositivo
    def proximo_comando_dispositivo():
        try:
            comando = entregar_proximo_comando(
                obter_banco(), request.args.get('dispositivo', '')
            )
            if comando is None:
                return jsonify(comando=None, intervalo_ms=2000, servidor_em=int(time.time()))
            return jsonify(
                comando={
                    'id': comando['id'],
                    'tipo': comando['tipo'],
                    'expira_em': comando['expira_em'],
                },
                intervalo_ms=2000,
                servidor_em=int(time.time()),
            )
        except ErroComando as erro:
            return jsonify(erro=str(erro)), 400

    @aplicacao.post('/api/dispositivo/comandos/<identificador>/confirmar')
    @exigir_dispositivo
    def confirmar_comando_dispositivo(identificador):
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify(erro='Envie objeto JSON.'), 400
        try:
            comando = confirmar_comando(
                obter_banco(), identificador, dados.get('dispositivo'),
                dados.get('resultado'), dados.get('detalhe', ''),
            )
            return jsonify(comando=resposta_comando(comando))
        except ErroComando as erro:
            mensagem = str(erro)
            codigo = 404 if mensagem == 'Comando não encontrado.' else 409
            return jsonify(erro=mensagem), codigo

    @aplicacao.get('/exportar/<tipo>')
    @exigir_login
    def exportar_csv(tipo):
        banco = obter_banco()
        consultas = {
            'livros': ('SELECT codigo,titulo,autor,categoria,isbn,uid,localizacao FROM livros ORDER BY codigo',
                       ['Código', 'Título', 'Autor', 'Categoria', 'ISBN', 'UID', 'Localização']),
            'usuarios': ('SELECT codigo,nome,email,uid,ativo FROM usuarios ORDER BY codigo',
                         ['Código', 'Nome', 'E-mail', 'UID', 'Ativo']),
            'emprestimos': ('''SELECT l.codigo,u.codigo,e.emprestado_em,e.devolvido_em,e.origem FROM emprestimos e
                JOIN livros l ON l.id=e.livro_id JOIN usuarios u ON u.id=e.usuario_id ORDER BY e.id DESC''',
                            ['Livro', 'Usuário', 'Empréstimo', 'Devolução', 'Origem']),
        }
        if tipo not in consultas:
            abort(404)
        comando, colunas = consultas[tipo]
        saida = io.StringIO()
        escritor = csv.writer(saida, delimiter=';')
        escritor.writerow(colunas)
        for linha in banco.execute(comando):
            escritor.writerow(list(linha))
        return Response('\ufeff' + saida.getvalue(), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename=bibliosocial_{tipo}.csv'})

    return aplicacao
