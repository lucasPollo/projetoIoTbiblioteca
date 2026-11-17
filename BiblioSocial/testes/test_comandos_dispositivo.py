"""Testes da fila HTTPS entre painel, simulador e dispositivo."""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from bibliosocial import criar_aplicacao
from bibliosocial.banco import abrir_conexao


class TestesComandosDispositivo(unittest.TestCase):
    def setUp(self):
        self.ambiente = tempfile.TemporaryDirectory()
        self.caminho = str(Path(self.ambiente.name) / 'teste.sqlite3')
        self.app = criar_aplicacao({
            'TESTING': True,
            'SECRET_KEY': 'chave-temporaria-de-testes-comprida',
            'CHAVE_DISPOSITIVO': 'chave-esp32-para-testes',
            'CAMINHO_BANCO': self.caminho,
            'DISPOSITIVO_PADRAO': 'esp32-principal',
            'PRAZO_COMANDO_SEGUNDOS': 45,
        })
        banco = abrir_conexao(self.caminho)
        with banco:
            banco.execute(
                'INSERT INTO administradores(usuario,senha_hash) VALUES(?,?)',
                ('admin', generate_password_hash('SenhaSegura123')),
            )
            banco.execute(
                'INSERT INTO usuarios(codigo,nome,uid) VALUES(?,?,?)',
                ('USR01', 'Pessoa de teste', '04216272D52A81'),
            )
            banco.execute(
                'INSERT INTO livros(codigo,titulo,autor,categoria,uid) VALUES(?,?,?,?,?)',
                ('LIV001', 'Livro de teste', 'Autor', 'Software', '04CDFD2DCE2A81'),
            )
        banco.close()
        self.cliente = self.app.test_client()

    def tearDown(self):
        self.ambiente.cleanup()

    def autenticar(self):
        self.cliente.get('/login')
        with self.cliente.session_transaction() as sessao:
            token = sessao['csrf_token']
        resposta = self.cliente.post('/login', data={
            'usuario': 'admin', 'senha': 'SenhaSegura123', 'csrf_token': token,
        })
        self.assertEqual(resposta.status_code, 302)
        with self.cliente.session_transaction() as sessao:
            return sessao['csrf_token']

    @staticmethod
    def cabecalho_dispositivo():
        return {'X-Chave-Dispositivo': 'chave-esp32-para-testes'}

    def criar_comando(self, token, tipo='led_verde'):
        return self.cliente.post(
            '/api/comandos', json={'tipo': tipo}, headers={'X-CSRF-Token': token}
        )

    def test_fluxo_entrega_confirmacao_e_idempotencia(self):
        self.cliente.get('/login')
        with self.cliente.session_transaction() as sessao:
            token_publico = sessao['csrf_token']
        bloqueado = self.criar_comando(token_publico)
        self.assertEqual(bloqueado.status_code, 401)
        configuracao_bloqueada = self.cliente.post(
            '/api/configuracoes/sinalizacao-simulacao', json={'ativa': True},
            headers={'X-CSRF-Token': token_publico},
        )
        self.assertEqual(configuracao_bloqueada.status_code, 401)

        token = self.autenticar()
        pagina = self.cliente.get('/operacao').get_data(as_text=True)
        self.assertIn('Testes do dispositivo', pagina)
        self.assertIn('Testar LED verde', pagina)
        self.assertNotIn('chave-esp32-para-testes', pagina)
        invalido = self.criar_comando(token, 'abrir_porta')
        self.assertEqual(invalido.status_code, 400)

        criado = self.criar_comando(token)
        self.assertEqual(criado.status_code, 202)
        comando = criado.json['comando']
        self.assertEqual(comando['estado'], 'pendente')
        self.assertTrue(comando['id'])
        self.assertNotIn('CHAVE_DISPOSITIVO', criado.get_data(as_text=True))

        sem_chave = self.cliente.get(
            '/api/dispositivo/comandos/proximo?dispositivo=esp32-principal'
        )
        self.assertEqual(sem_chave.status_code, 401)

        entregue = self.cliente.get(
            '/api/dispositivo/comandos/proximo?dispositivo=esp32-principal',
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(entregue.status_code, 200)
        self.assertEqual(entregue.json['comando']['id'], comando['id'])
        self.assertEqual(entregue.json['comando']['tipo'], 'led_verde')

        repetido = self.cliente.get(
            '/api/dispositivo/comandos/proximo?dispositivo=esp32-principal',
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(repetido.json['comando']['id'], comando['id'])

        outro_dispositivo = self.cliente.post(
            f"/api/dispositivo/comandos/{comando['id']}/confirmar",
            json={'dispositivo': 'esp32-secundaria', 'resultado': 'executado'},
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(outro_dispositivo.status_code, 409)

        confirmacao = self.cliente.post(
            f"/api/dispositivo/comandos/{comando['id']}/confirmar",
            json={
                'dispositivo': 'esp32-principal',
                'resultado': 'executado',
                'detalhe': 'LED acionado por 2 segundos',
            },
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(confirmacao.status_code, 200)
        self.assertEqual(confirmacao.json['comando']['estado'], 'executado')

        confirmacao_repetida = self.cliente.post(
            f"/api/dispositivo/comandos/{comando['id']}/confirmar",
            json={'dispositivo': 'esp32-principal', 'resultado': 'executado'},
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(confirmacao_repetida.status_code, 200)
        self.assertEqual(confirmacao_repetida.json['comando']['estado'], 'executado')

        vazio = self.cliente.get(
            '/api/dispositivo/comandos/proximo?dispositivo=esp32-principal',
            headers=self.cabecalho_dispositivo(),
        )
        self.assertIsNone(vazio.json['comando'])
        estado = self.cliente.get(f"/api/comandos/{comando['id']}")
        self.assertEqual(estado.json['comando']['estado'], 'executado')

    def test_expiracao_e_falha_reportada(self):
        token = self.autenticar()
        criado = self.criar_comando(token, 'buzzer').json['comando']
        banco = abrir_conexao(self.caminho)
        with banco:
            banco.execute(
                'UPDATE comandos_dispositivo SET expira_em=0 WHERE id=?',
                (criado['id'],),
            )
        banco.close()
        estado = self.cliente.get(f"/api/comandos/{criado['id']}")
        self.assertEqual(estado.json['comando']['estado'], 'expirado')

        falho = self.criar_comando(token, 'led_vermelho').json['comando']
        self.cliente.get(
            '/api/dispositivo/comandos/proximo?dispositivo=esp32-principal',
            headers=self.cabecalho_dispositivo(),
        )
        resposta = self.cliente.post(
            f"/api/dispositivo/comandos/{falho['id']}/confirmar",
            json={
                'dispositivo': 'esp32-principal', 'resultado': 'falhou',
                'detalhe': 'GPIO indisponível',
            },
            headers=self.cabecalho_dispositivo(),
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json['comando']['estado'], 'falhou')

    def test_simulacao_opcional_enfileira_sinais_e_marca_origem(self):
        token = self.autenticar()
        simulador = self.cliente.get('/simulador').get_data(as_text=True)
        self.assertIn('Ativar sinalização física nas simulações', simulador)
        sem_sinal = self.cliente.post(
            '/api/simulacao/leitura', json={'uid': 'UID-invalido'},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(sem_sinal.status_code, 400)
        banco = abrir_conexao(self.caminho)
        self.assertEqual(
            banco.execute('SELECT COUNT(*) FROM comandos_dispositivo').fetchone()[0], 0
        )
        banco.close()

        ativacao = self.cliente.post(
            '/api/configuracoes/sinalizacao-simulacao', json={'ativa': True},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(ativacao.status_code, 200)
        self.assertTrue(ativacao.json['ativa'])

        inicio = self.cliente.post(
            '/api/reservas/iniciar', json={'livro_id': 1},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(inicio.status_code, 200)
        livro = self.cliente.post(
            '/api/simulacao/leitura', json={'uid': '04CDFD2DCE2A81'},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(livro.json['resultado'], 'aguardando')
        usuario = self.cliente.post(
            '/api/simulacao/leitura', json={'uid': '04216272D52A81'},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(usuario.status_code, 200)
        self.assertTrue(usuario.json['reserva_simulada'])
        self.assertIn('Reserva realizada por simulação', usuario.json['mensagem'])
        self.assertEqual(usuario.json['sinalizacao_fisica'], 'pendente')

        banco = abrir_conexao(self.caminho)
        reserva = banco.execute('SELECT origem FROM reservas').fetchone()
        comando = banco.execute(
            'SELECT tipo,origem,estado FROM comandos_dispositivo'
        ).fetchone()
        self.assertEqual(reserva['origem'], 'simulacao')
        self.assertEqual(tuple(comando), ('sinal_sucesso', 'simulacao', 'pendente'))
        banco.close()

        erro = self.cliente.post(
            '/api/simulacao/leitura', json={'uid': 'UID-invalido'},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(erro.status_code, 400)
        banco = abrir_conexao(self.caminho)
        self.assertEqual(
            banco.execute(
                "SELECT COUNT(*) FROM comandos_dispositivo WHERE tipo='sinal_erro'"
            ).fetchone()[0], 1
        )
        banco.close()


if __name__ == '__main__':
    unittest.main()
