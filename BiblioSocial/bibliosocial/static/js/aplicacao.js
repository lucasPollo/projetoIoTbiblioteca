/* Interações da interface, captura RFID, simulação e reserva. */
(() => {
  'use strict';

  const token_csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const botao_menu = document.getElementById('alternar-menu');
  const menu_lateral = document.getElementById('menu-lateral');
  const fundo_menu = document.getElementById('fundo-menu');

  const fechar_menu = () => {
    menu_lateral?.classList.remove('aberta');
    fundo_menu?.classList.remove('visivel');
    botao_menu?.setAttribute('aria-expanded', 'false');
  };

  botao_menu?.addEventListener('click', () => {
    const aberto = menu_lateral?.classList.toggle('aberta') || false;
    fundo_menu?.classList.toggle('visivel', aberto);
    botao_menu.setAttribute('aria-expanded', String(aberto));
  });
  fundo_menu?.addEventListener('click', fechar_menu);
  window.addEventListener('resize', () => { if (window.innerWidth > 760) fechar_menu(); });

  const requisitar = async (endereco, metodo, dados) => {
    const resposta = await fetch(endereco, {
      method: metodo,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token_csrf },
      credentials: 'same-origin',
      body: metodo === 'GET' ? undefined : JSON.stringify(dados || {})
    });
    const retorno = await resposta.json().catch(() => ({}));
    if (!resposta.ok) throw new Error(retorno.erro || 'Não foi possível concluir a solicitação.');
    return retorno;
  };

  const definir_carregamento = (botao, carregando, texto = 'Aguarde...') => {
    if (!botao) return;
    if (carregando) {
      botao.dataset.textoOriginal = botao.textContent;
      botao.textContent = texto;
      botao.disabled = true;
      botao.setAttribute('aria-busy', 'true');
    } else {
      botao.textContent = botao.dataset.textoOriginal || botao.textContent;
      botao.disabled = false;
      botao.removeAttribute('aria-busy');
    }
  };

  for (const formulario of document.querySelectorAll('[data-form-captura]')) {
    const tipo = formulario.dataset.formCaptura;
    const botao = formulario.querySelector('[data-acao="capturar"]');
    const campo_uid = formulario.querySelector('[data-uid-alvo]');
    const mensagem = formulario.querySelector('.resultado-captura');
    if (!botao || !campo_uid || !mensagem) continue;

    botao.addEventListener('click', async () => {
      definir_carregamento(botao, true, 'Aguardando leitura...');
      try {
        const captura = await requisitar('/api/captura/iniciar', 'POST', { tipo });
        mensagem.textContent = 'Aproxime o cartão ou a etiqueta do leitor.';
        for (let tentativa = 0; tentativa < 39; tentativa++) {
          await new Promise(resolver => setTimeout(resolver, 2000));
          const estado = await requisitar('/api/captura/' + encodeURIComponent(captura.identificador), 'GET');
          if (estado.estado === 'capturado') {
            campo_uid.value = estado.uid;
            mensagem.textContent = 'UID capturado. Salve o cadastro para concluir.';
            return;
          }
          if (estado.estado !== 'aguardando') {
            mensagem.textContent = 'Captura encerrada. Tente novamente.';
            return;
          }
        }
        mensagem.textContent = 'Tempo encerrado. Tente novamente.';
      } catch (erro) {
        mensagem.textContent = erro.message;
      } finally {
        definir_carregamento(botao, false);
      }
    });
  }

  const campo_simulacao = document.getElementById('uid-simulacao');
  const botao_simulacao = document.getElementById('enviar-simulacao');
  const retorno_simulacao = document.getElementById('retorno-simulacao');
  const alternador_sinalizacao = document.getElementById('sinalizacao-fisica-simulacao');
  const status_sinalizacao = document.getElementById('status-sinalizacao-simulacao');

  alternador_sinalizacao?.addEventListener('change', async () => {
    const valor_anterior = !alternador_sinalizacao.checked;
    alternador_sinalizacao.disabled = true;
    if (status_sinalizacao) status_sinalizacao.textContent = 'Salvando configuração...';
    try {
      const retorno = await requisitar('/api/configuracoes/sinalizacao-simulacao', 'POST', {
        ativa: alternador_sinalizacao.checked
      });
      if (status_sinalizacao) {
        status_sinalizacao.textContent = retorno.ativa
          ? 'Sinalização física ativada.'
          : 'Sinalização física desativada.';
      }
    } catch (erro) {
      alternador_sinalizacao.checked = valor_anterior;
      if (status_sinalizacao) status_sinalizacao.textContent = erro.message;
    } finally {
      alternador_sinalizacao.disabled = false;
    }
  });

  for (const identificador of document.querySelectorAll('[data-uid]')) {
    identificador.addEventListener('click', () => {
      if (!campo_simulacao) return;
      campo_simulacao.value = identificador.dataset.uid;
      campo_simulacao.focus();
    });
  }

  botao_simulacao?.addEventListener('click', async () => {
    if (!campo_simulacao?.value.trim() || !retorno_simulacao) return;
    definir_carregamento(botao_simulacao, true, 'Enviando...');
    try {
      const resultado = await requisitar('/api/simulacao/leitura', 'POST', { uid: campo_simulacao.value.trim() });
      const sufixo_fisico = resultado.comando_id
        ? ' Sinal físico pendente na fila da ESP32.'
        : resultado.sinalizacao_fisica === 'falhou'
          ? ' A operação foi concluída, mas não foi possível enfileirar o sinal físico.'
          : '';
      retorno_simulacao.textContent = 'Leitura simulada: ' + (resultado.mensagem || 'enviada.') + sufixo_fisico;
      retorno_simulacao.className = 'retorno-simulacao ' + (resultado.resultado === 'erro' ? 'erro' : 'sucesso');
    } catch (erro) {
      retorno_simulacao.textContent = erro.message;
      retorno_simulacao.className = 'retorno-simulacao erro';
    } finally {
      definir_carregamento(botao_simulacao, false);
    }
  });

  const rotulos_comando = {
    pendente: 'Pendente',
    entregue: 'Pendente · recebido pela ESP32',
    executado: 'Executado',
    expirado: 'Expirado',
    falhou: 'Falhou'
  };

  const exibir_status_comando = (elemento, comando) => {
    if (!elemento || !comando) return;
    elemento.textContent = rotulos_comando[comando.estado] || comando.estado;
    elemento.className = 'estado-comando ' + comando.estado;
    if (comando.detalhe) elemento.title = comando.detalhe;
  };

  const acompanhar_comando = async (comando_inicial, elemento) => {
    let comando = comando_inicial;
    exibir_status_comando(elemento, comando);
    while (!['executado', 'expirado', 'falhou'].includes(comando.estado)) {
      await new Promise(resolver => setTimeout(resolver, 2000));
      try {
        const retorno = await requisitar('/api/comandos/' + encodeURIComponent(comando.id), 'GET');
        comando = retorno.comando;
        exibir_status_comando(elemento, comando);
      } catch (_) {
        if (elemento) {
          elemento.textContent = 'Pendente · reconectando ao servidor';
          elemento.className = 'estado-comando pendente';
        }
      }
      if (Date.now() / 1000 > Number(comando.expira_em || 0) + 15) {
        if (elemento && !['executado', 'falhou'].includes(comando.estado)) {
          elemento.textContent = 'Expirado';
          elemento.className = 'estado-comando expirado';
        }
        break;
      }
    }
  };

  for (const botao of document.querySelectorAll('[data-comando-dispositivo]')) {
    botao.addEventListener('click', async () => {
      const tipo = botao.dataset.comandoDispositivo;
      const status = document.querySelector(`[data-status-comando="${tipo}"]`);
      definir_carregamento(botao, true, 'Enviando...');
      try {
        const retorno = await requisitar('/api/comandos', 'POST', { tipo });
        await acompanhar_comando(retorno.comando, status);
      } catch (erro) {
        if (status) {
          status.textContent = 'Falhou: ' + erro.message;
          status.className = 'estado-comando falhou';
        }
      } finally {
        definir_carregamento(botao, false);
      }
    });
  }

  const janela_reserva = document.getElementById('reserva-janela');
  const status_reserva = document.getElementById('reserva-status');
  const titulo_reserva = document.getElementById('reserva-livro-nome');
  const passos_reserva = [...document.querySelectorAll('[data-reserva-passo]')];
  let token_reserva = '';
  let contador_reserva = 0;
  let foco_anterior = null;
  let botao_reserva_ativo = null;
  const pausar = milissegundos => new Promise(resolver => setTimeout(resolver, milissegundos));

  const atualizar_passo = etapa => {
    for (const passo of passos_reserva) {
      const numero = Number(passo.dataset.reservaPasso);
      passo.classList.toggle('ativo', numero === etapa);
      passo.classList.toggle('concluido', numero < etapa || etapa > 3);
    }
  };

  const restaurar_botao_reserva = botao => {
    const botao_alvo = botao || botao_reserva_ativo;
    if (botao_alvo) definir_carregamento(botao_alvo, false);
    if (botao_alvo === botao_reserva_ativo) botao_reserva_ativo = null;
  };

  const fechar_reserva = async () => {
    contador_reserva++;
    janela_reserva?.setAttribute('hidden', '');
    restaurar_botao_reserva();
    foco_anterior?.focus();
    if (!token_reserva) return;
    const token_anterior = token_reserva;
    token_reserva = '';
    try { await requisitar('/api/reservas/' + encodeURIComponent(token_anterior) + '/cancelar', 'POST', {}); } catch (_) { /* A reserva já pode ter encerrado. */ }
  };

  for (const fechar of document.querySelectorAll('[data-reserva-fechar]')) fechar.addEventListener('click', fechar_reserva);

  for (const botao of document.querySelectorAll('[data-reservar-livro]')) {
    botao.addEventListener('click', async () => {
      if (!janela_reserva || !status_reserva || botao.disabled) return;
      foco_anterior = botao;
      botao_reserva_ativo = botao;
      definir_carregamento(botao, true, 'Iniciando...');
      janela_reserva.removeAttribute('hidden');
      titulo_reserva.textContent = botao.dataset.tituloLivro;
      status_reserva.textContent = 'Preparando a reserva.';
      status_reserva.className = 'retorno-simulacao';
      atualizar_passo(1);
      janela_reserva.querySelector('[data-reserva-fechar]')?.focus();
      const versao = ++contador_reserva;

      try {
        const iniciado = await requisitar('/api/reservas/iniciar', 'POST', { livro_id: Number(botao.dataset.reservarLivro) });
        const identificador_iniciado = iniciado.identificador;
        if (versao !== contador_reserva) {
          try { await requisitar('/api/reservas/' + encodeURIComponent(identificador_iniciado) + '/cancelar', 'POST', {}); } catch (_) { /* A janela já foi fechada. */ }
          restaurar_botao_reserva(botao);
          return;
        }
        token_reserva = identificador_iniciado;
        atualizar_passo(2);
        status_reserva.textContent = 'Aproxime a etiqueta NFC do livro.';

        while (versao === contador_reserva) {
          await pausar(1400);
          if (versao !== contador_reserva) break;
          const estado = await requisitar('/api/reservas/' + encodeURIComponent(token_reserva), 'GET');

          if (estado.estado === 'aguardando_livro') {
            atualizar_passo(2);
            status_reserva.textContent = 'Aproxime a etiqueta NFC do livro.';
          } else if (estado.estado === 'aguardando_usuario') {
            atualizar_passo(3);
            status_reserva.textContent = 'Livro validado. Aproxime seu cartão pessoal.';
          } else if (estado.estado === 'concluida') {
            atualizar_passo(4);
            status_reserva.className = 'retorno-simulacao sucesso';
            status_reserva.textContent = 'Reserva confirmada.';
            token_reserva = '';
            await pausar(1200);
            window.location.reload();
            break;
          } else if (['expirada', 'cancelada'].includes(estado.estado)) {
            status_reserva.className = 'retorno-simulacao erro';
            status_reserva.textContent = estado.estado === 'expirada' ? 'O tempo terminou. Inicie uma nova reserva.' : 'A reserva foi cancelada.';
            token_reserva = '';
            restaurar_botao_reserva(botao);
            break;
          }
        }
      } catch (erro) {
        if (versao !== contador_reserva) {
          restaurar_botao_reserva(botao);
          return;
        }
        status_reserva.className = 'retorno-simulacao erro';
        status_reserva.textContent = erro.message;
        token_reserva = '';
        restaurar_botao_reserva(botao);
      }
    });
  }

  document.addEventListener('keydown', evento => {
    if (evento.key === 'Escape' && janela_reserva && !janela_reserva.hasAttribute('hidden')) fechar_reserva();
  });
})();
