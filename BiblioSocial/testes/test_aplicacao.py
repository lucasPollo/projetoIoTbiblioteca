"""Testes da API Flask e empréstimos, executados após instalar requirements.txt.
python -m unittest discover -s testes -v
"""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash
from bibliosocial import criar_aplicacao
from bibliosocial.banco import abrir_conexao


class TestesBiblioSocial(unittest.TestCase):
    def setUp(self):
        self.ambiente = tempfile.TemporaryDirectory()
        caminho = str(Path(self.ambiente.name) / 'teste.sqlite3')
        self.app = criar_aplicacao({
            'TESTING': True, 'SECRET_KEY': 'chave-temporaria-de-testes-comprida',
            'CHAVE_DISPOSITIVO': 'chave-esp32-para-testes', 'CAMINHO_BANCO': caminho,
        })
        banco = abrir_conexao(caminho)
        with banco:
            banco.execute('INSERT INTO administradores(usuario,senha_hash) VALUES(?,?)',
                          ('admin', generate_password_hash('SenhaSegura123')))
            banco.execute('INSERT INTO usuarios(codigo,nome,uid) VALUES(?,?,?)',
                          ('USR01', 'Pessoa de teste', '04216272D52A81'))
            banco.execute('INSERT INTO livros(codigo,titulo,autor,categoria,uid) VALUES(?,?,?,?,?)',
                          ('LIV001', 'Livro de teste', 'Autor', 'Software', '04CDFD2DCE2A81'))
        banco.close()
        self.caminho = caminho
        self.cliente = self.app.test_client()

    def tearDown(self):
        self.ambiente.cleanup()

    def autenticar(self):
        self.cliente.get('/login')
        with self.cliente.session_transaction() as sessao:
            token = sessao['csrf_token']
        resposta = self.cliente.post('/login', data={'usuario': 'admin', 'senha': 'SenhaSegura123',
                                                    'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        return token

    def cabecalho_dispositivo(self):
        return {'X-Chave-Dispositivo': 'chave-esp32-para-testes'}

    def testar_login_e_painel(self):
        self.assertEqual(self.cliente.get('/').status_code, 302)
        self.autenticar()
        self.assertIn('Biblioteca em tempo real', self.cliente.get('/').get_data(as_text=True))
        self.assertIn('Livro de teste', self.cliente.get('/livros').get_data(as_text=True))

    def testar_api_exige_chave(self):
        resposta = self.cliente.post('/api/dispositivo/leitura', json={'uid': '04216272D52A81'})
        self.assertEqual(resposta.status_code, 401)

    def testar_emprestimo_e_devolucao_rfid(self):
        self.autenticar()
        with self.cliente.session_transaction() as sessao:
            token = sessao['csrf_token']
        resposta = self.cliente.post('/operacao', data={'csrf_token': token, 'modo':'emprestimo'})
        self.assertEqual(resposta.status_code, 302)
        for uid in ('04216272D52A81', '04CDFD2DCE2A81'):
            resposta = self.cliente.post('/api/dispositivo/leitura', json={'uid':uid},
                                         headers=self.cabecalho_dispositivo())
            self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json['resultado'], 'ok')
        banco = abrir_conexao(self.caminho)
        self.assertEqual(banco.execute('SELECT COUNT(*) FROM emprestimos WHERE devolvido_em IS NULL').fetchone()[0], 1)
        banco.close()
        self.cliente.post('/operacao', data={'csrf_token': token, 'modo':'devolucao'})
        for uid in ('04216272D52A81', '04CDFD2DCE2A81'):
            resposta = self.cliente.post('/api/dispositivo/leitura', json={'uid':uid},
                                         headers=self.cabecalho_dispositivo())
        self.assertEqual(resposta.json['resultado'], 'ok')
        banco = abrir_conexao(self.caminho)
        self.assertEqual(banco.execute('SELECT COUNT(*) FROM emprestimos WHERE devolvido_em IS NULL').fetchone()[0], 0)
        banco.close()

    def testar_captura_uid_e_idempotencia(self):
        self.autenticar()
        with self.cliente.session_transaction() as sessao:
            token = sessao['csrf_token']
        resposta = self.cliente.post('/api/captura/iniciar', json={'tipo':'livro'},
                                     headers={'X-CSRF-Token':token})
        self.assertEqual(resposta.status_code, 200)
        identificador = resposta.json['identificador']
        dados = {'uid': '04CDFD2DCE2A81', 'evento_id': 'evento-teste-idempotente'}
        resposta = self.cliente.post('/api/dispositivo/leitura', json=dados,
                                     headers=self.cabecalho_dispositivo())
        self.assertEqual(resposta.json['resultado'], 'capturado')
        repetida = self.cliente.post('/api/dispositivo/leitura', json=dados,
                                     headers=self.cabecalho_dispositivo())
        self.assertEqual(repetida.json, resposta.json)
        estado = self.cliente.get('/api/captura/' + identificador)
        self.assertEqual(estado.json['uid'], '04CDFD2DCE2A81')


if __name__ == '__main__':
    unittest.main()
