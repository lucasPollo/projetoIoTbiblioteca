"""Ponto de entrada HTTP do BiblioSocial IoT."""
import os
from bibliosocial import criar_aplicacao

aplicacao = criar_aplicacao()

if __name__ == '__main__':
    aplicacao.run(host=os.environ.get('HOST', '0.0.0.0'), port=int(os.environ.get('PORTA', '5000')), debug=False)
