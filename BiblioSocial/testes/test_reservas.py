"""Testes do fluxo de reservas: livro → cartão → reserva ativa → retirada."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from bibliosocial.servicos import (ErroNegocio, cancelar_reserva, consultar_reserva_pendente,
    emprestar_livro, iniciar_reserva, processar_leitura, iniciar_captura)

CAMINHO_ESQUEMA = Path(__file__).resolve().parents[1] / 'bibliosocial' / 'esquema.sql'

class TestesReservaRFID(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.banco = sqlite3.connect(str(Path(self.temporario.name) / 'teste.db'))
        self.banco.row_factory = sqlite3.Row
        self.banco.execute('PRAGMA foreign_keys=ON')
        self.banco.executescript(CAMINHO_ESQUEMA.read_text(encoding='utf-8'))
        self.banco.execute("INSERT INTO livros(codigo,titulo,uid) VALUES('LIV001','Livro 1','04112233445566')")
        self.banco.execute("INSERT INTO livros(codigo,titulo,uid) VALUES('LIV002','Livro 2','04223344556677')")
        self.banco.execute("INSERT INTO usuarios(codigo,nome,uid) VALUES('USR01','Usuário 1','04334455667788')")
        self.banco.execute("INSERT INTO usuarios(codigo,nome,uid) VALUES('USR02','Usuário 2','04445566778899')")
        self.banco.commit()

    def tearDown(self):
        self.banco.close()
        self.temporario.cleanup()

    def leitura(self, uid, numero):
        return processar_leitura(self.banco,uid,evento_id=f'teste-reserva-{numero}',dispositivo='simulador')

    def test_reserva_confirmada_com_etiqueta_e_cartao(self):
        identificador = iniciar_reserva(self.banco,1)
        self.assertEqual(self.leitura('04334455667788',1)['resultado'],'erro')
        self.assertEqual(self.leitura('04223344556677',2)['resultado'],'erro')
        self.assertEqual(self.leitura('04112233445566',3)['resultado'],'aguardando')
        self.assertEqual(self.leitura('04334455667788',4)['resultado'],'ok')
        self.assertEqual(consultar_reserva_pendente(self.banco,identificador)['estado'],'concluida')
        self.assertEqual(self.banco.execute("SELECT situacao FROM visao_livros WHERE id=1").fetchone()[0], 'Reservado')
        self.assertEqual(self.leitura('04334455667788',4)['resultado'],'ok')
        with self.assertRaises(ErroNegocio): iniciar_reserva(self.banco,1)
        with self.assertRaises(ErroNegocio): emprestar_livro(self.banco,1,2)
        with self.banco: emprestar_livro(self.banco,1,1)
        self.assertEqual(self.banco.execute("SELECT situacao FROM reservas WHERE livro_id=1").fetchone()[0], 'retirada')
        self.assertEqual(self.banco.execute("SELECT situacao FROM visao_livros WHERE id=1").fetchone()[0], 'Emprestado')

    def test_cancelar_e_liberar_livro(self):
        iniciar_reserva(self.banco,1)
        self.leitura('04112233445566',1)
        self.leitura('04334455667788',2)
        with self.banco: cancelar_reserva(self.banco,1)
        self.assertEqual(self.banco.execute("SELECT situacao FROM visao_livros WHERE id=1").fetchone()[0], 'Disponível')
        self.assertTrue(iniciar_reserva(self.banco,1))

    def test_leitor_exclusivo_para_uma_sessao(self):
        iniciar_reserva(self.banco,1)
        with self.assertRaises(ErroNegocio): iniciar_reserva(self.banco,2)
        with self.assertRaises(ErroNegocio):
            with self.banco: iniciar_captura(self.banco,'livro')

if __name__ == '__main__': unittest.main()
