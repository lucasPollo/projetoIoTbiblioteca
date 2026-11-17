"""Configuração inicial: credenciais do painel e chaves do ambiente."""
from __future__ import annotations

import getpass
import os
import secrets
import sqlite3
from pathlib import Path

from dotenv import dotenv_values
from werkzeug.security import generate_password_hash

from bibliosocial.banco import inicializar_banco

RAIZ = Path(__file__).resolve().parent
ARQUIVO_AMBIENTE = RAIZ / '.env'
CAMINHO_BANCO = RAIZ / 'dados' / 'bibliosocial.db'


def configurar_ambiente() -> dict:
    anterior = dotenv_values(ARQUIVO_AMBIENTE) if ARQUIVO_AMBIENTE.exists() else {}
    chave_sessao = anterior.get('CHAVE_SESSAO', '')
    chave_dispositivo = anterior.get('CHAVE_DISPOSITIVO', '')
    if len(chave_sessao) < 24 or chave_sessao.upper().startswith('GERE_'):
        chave_sessao = secrets.token_urlsafe(48)
    if len(chave_dispositivo) < 24 or chave_dispositivo.upper().startswith('GERE_'):
        chave_dispositivo = secrets.token_urlsafe(40)
    dados = {
        'CHAVE_SESSAO': chave_sessao,
        'CHAVE_DISPOSITIVO': chave_dispositivo,
        'DISPOSITIVO_PADRAO': anterior.get('DISPOSITIVO_PADRAO', 'esp32-principal'),
        'PRAZO_COMANDO_SEGUNDOS': anterior.get('PRAZO_COMANDO_SEGUNDOS', '45'),
        'CAMINHO_BANCO': 'dados/bibliosocial.db',
        'HOST': '0.0.0.0',
        'PORTA': '5000',
        'COOKIE_SEGURO': 'false',
    }
    ARQUIVO_AMBIENTE.write_text('\n'.join(f'{campo}={valor}' for campo, valor in dados.items()) + '\n', encoding='utf-8')
    return dados


def cadastrar_administrador() -> None:
    inicializar_banco(str(CAMINHO_BANCO))
    conexao = sqlite3.connect(CAMINHO_BANCO)
    try:
        atual = conexao.execute('SELECT COUNT(*) FROM administradores').fetchone()[0]
        print('\n=== Administrador do BiblioSocial ===')
        if atual:
            substituir = input('Já existe administrador. Criar/atualizar outro? (s/N): ').strip().lower()
            if substituir != 's':
                return
        usuario = input('Nome de usuário administrativo [admin]: ').strip() or 'admin'
        while True:
            senha = getpass.getpass('Senha (mínimo 8 caracteres): ')
            confirmar = getpass.getpass('Confirme a senha: ')
            if senha != confirmar or len(senha) < 8:
                print('Senhas diferentes ou muito curtas. Tente novamente.')
                continue
            break
        conexao.execute('''INSERT INTO administradores(usuario,senha_hash) VALUES(?,?)
            ON CONFLICT(usuario) DO UPDATE SET senha_hash=excluded.senha_hash''',
            (usuario, generate_password_hash(senha)))
        conexao.commit()
        print('Administrador configurado.')
    finally:
        conexao.close()


if __name__ == '__main__':
    configurar_ambiente()
    cadastrar_administrador()
    print('\nConfiguração concluída. Agora execute: python app.py')
