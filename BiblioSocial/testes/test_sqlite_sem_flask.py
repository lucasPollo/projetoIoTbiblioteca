"""Teste básico de integridade SQLite, executável mesmo sem Flask instalado."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

ORIGEM = Path(__file__).resolve().parents[1] / 'dados' / 'bibliosocial.db'
ESQUEMA = Path(__file__).resolve().parents[1] / 'bibliosocial' / 'esquema.sql'


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

    def test_migracao_aditiva_preserva_dados(self):
        tabelas = ('livros', 'usuarios', 'emprestimos', 'reservas', 'leituras')
        contagens_antes = {
            tabela: self.conexao.execute(f'SELECT COUNT(*) FROM {tabela}').fetchone()[0]
            for tabela in tabelas
        }
        self.conexao.executescript(ESQUEMA.read_text(encoding='utf-8'))
        contagens_depois = {
            tabela: self.conexao.execute(f'SELECT COUNT(*) FROM {tabela}').fetchone()[0]
            for tabela in tabelas
        }
        self.assertEqual(contagens_depois, contagens_antes)
        self.assertEqual(self.conexao.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.conexao.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        novas = self.conexao.execute("""SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name IN ('comandos_dispositivo','configuracoes')""").fetchone()[0]
        self.assertEqual(novas, 2)


if __name__ == '__main__':
    unittest.main()
