"""Teste básico de integridade SQLite, executável mesmo sem Flask instalado."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

ORIGEM = Path(__file__).resolve().parents[1] / 'dados' / 'bibliosocial.db'


class TestesBanco(unittest.TestCase):
    def setUp(self):
        self.diretorio = tempfile.TemporaryDirectory()
        self.arquivo = Path(self.diretorio.name) / 'teste.db'
        shutil.copy2(ORIGEM, self.arquivo)
        self.conexao = sqlite3.connect(self.arquivo)
        self.conexao.execute('PRAGMA foreign_keys=ON')

    def tearDown(self):
        self.conexao.close()
        self.diretorio.cleanup()

    def test_importacao_inicial(self):
        self.assertEqual(self.conexao.execute('SELECT COUNT(*) FROM livros').fetchone()[0], 5)
        self.assertEqual(self.conexao.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0], 3)
        self.assertEqual(self.conexao.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_uid_nao_pode_ser_reutilizado(self):
        uid = self.conexao.execute('SELECT uid FROM livros LIMIT 1').fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conexao.execute("INSERT INTO usuarios(codigo,nome,uid) VALUES('USR99','Teste',?)", (uid,))

    def test_emprestimo_unico(self):
        livro_id = self.conexao.execute('SELECT id FROM livros LIMIT 1').fetchone()[0]
        usuario_id = self.conexao.execute('SELECT id FROM usuarios LIMIT 1').fetchone()[0]
        self.conexao.execute('INSERT INTO emprestimos(livro_id,usuario_id) VALUES(?,?)', (livro_id,usuario_id))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conexao.execute('INSERT INTO emprestimos(livro_id,usuario_id) VALUES(?,?)', (livro_id,usuario_id))


if __name__ == '__main__':
    unittest.main()
