"""Importa ou atualiza livros e usuários a partir dos CSVs entregues no projeto.

Utilização: python importar_dados.py
Edite documentos/livros.csv e documentos/usuarios.csv antes de executar.
"""
import csv
import sqlite3
from pathlib import Path
from bibliosocial.banco import abrir_conexao, inicializar_banco
from bibliosocial.servicos import normalizar_uid

RAIZ = Path(__file__).resolve().parent
BANCO = RAIZ / 'dados' / 'bibliosocial.db'


def ler_csv(nome):
    with (RAIZ / 'documentos' / nome).open(encoding='utf-8-sig', newline='') as arquivo:
        return list(csv.DictReader(arquivo, delimiter=';'))


def importar():
    inicializar_banco(str(BANCO))
    banco = abrir_conexao(str(BANCO))
    contagens = {'livros': 0, 'usuarios': 0}
    try:
        with banco:
            for livro in ler_csv('livros.csv'):
                if not livro.get('codigo', '').strip() or not livro.get('titulo', '').strip():
                    continue
                banco.execute('''INSERT INTO livros(codigo,titulo,autor,categoria,isbn,uid,localizacao)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(codigo) DO UPDATE SET
                    titulo=excluded.titulo,autor=excluded.autor,categoria=excluded.categoria,
                    isbn=excluded.isbn,uid=excluded.uid,localizacao=excluded.localizacao''',
                    (livro['codigo'].strip().upper(), livro['titulo'].strip(), livro.get('autor','').strip(),
                     livro.get('categoria','Geral').strip() or 'Geral', livro.get('isbn','').strip(),
                     normalizar_uid(livro.get('uid')), livro.get('localizacao','').strip()))
                contagens['livros'] += 1
            for usuario in ler_csv('usuarios.csv'):
                if not usuario.get('codigo','').strip() or not usuario.get('nome','').strip():
                    continue
                banco.execute('''INSERT INTO usuarios(codigo,nome,email,uid,ativo) VALUES(?,?,?,?,?)
                    ON CONFLICT(codigo) DO UPDATE SET nome=excluded.nome,email=excluded.email,
                    uid=excluded.uid,ativo=excluded.ativo''',
                    (usuario['codigo'].strip().upper(), usuario['nome'].strip(),
                     usuario.get('email','').strip(), normalizar_uid(usuario.get('uid')),
                     1 if usuario.get('ativo','1').strip() != '0' else 0))
                contagens['usuarios'] += 1
        print(f"Importação concluída: {contagens['livros']} livros e {contagens['usuarios']} usuários.")
    finally:
        banco.close()


if __name__ == '__main__':
    importar()
