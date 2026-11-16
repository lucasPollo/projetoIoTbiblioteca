## Novo — Reservas por RFID (livro → cartão)

Além do painel administrativo, visite **`http://IP_DO_COMPUTADOR:5000/catalogo`** para abrir o catálogo responsivo para visitantes.
Cada livro aparece em um card com o botão **Reservar**, habilitado apenas se estiver disponível e tiver UID cadastrado.

1. Clique **Reservar** no card do livro desejado.
2. Em até **150 segundos**, passe a etiqueta NFC **desse livro** no RC522.
3. Passe seu cartão pessoal **USR01, USR02 ou USR03** no RC522.
4. O SQLite registra uma **reserva ativa**, distinta do empréstimo. O painel mostra o livro como **Reservado**.
5. No menu **Reservas**, o administrador pode **cancelar** ou **registrar retirada** (converter em empréstimo).

A reserva só é gravada após a leitura correta do livro e do usuário. O sistema rejeita um livro diferente,
cartão inativo, exemplar já reservado e tentativas simultâneas no mesmo RC522.
A sessão de confirmação é exclusiva e temporária: **uma por vez por estação**.
A tela acompanha o status via AJAX e atualiza o catálogo após a confirmação.
No simulador do painel, após clicar Reservar, envie primeiro o UID de um **livro**, depois o UID do **cartão**.
**Não feche a janela da reserva durante o teste:** ela acompanha a leitura e informa a confirmação.
O dispositivo conectado recebe a confirmação somente quando a reserva é concluída.
**Não compartilhe a sessão de administrador com visitantes**: o endereço `/catalogo` é público na rede local,
mas o cadastro e o gerenciamento continuam restritos ao administrador.
Não disponibilize o servidor de desenvolvimento diretamente na internet.

### Observações sobre o estado dos livros

- **Disponível**: pode iniciar uma reserva.
- **Reservado**: existe uma reserva ativa, aguarda retirada; outro usuário não pode reservar ou emprestar.
- **Emprestado**: um empréstimo foi registrado; exige devolução para ficar disponível.
- Registrar a retirada converte a reserva em empréstimo para o mesmo cartão.
- A troca de modo da estação para Empréstimo continua funcionando no fluxo antigo (cartão → livro).

---

# BiblioSocial IoT — Sistema completo

Projeto acadêmico de biblioteca inteligente. Inclui **backend Python/Flask**, **frontend HTML/CSS/JavaScript** e **banco SQLite populado com a sua planilha**. O firmware do dispositivo não faz parte deste repositório.

## Dados reais já cadastrados

O arquivo `dados/bibliosocial.db` já contém:
- **5 livros** importados de `documentos/cadastroCompleto.xlsx`, com título, autor, categoria e UID de cada etiqueta NTAG215.
- **3 usuários e cartões** importados com UIDs (Lucas, Higor e Juliane).
- Categorias: **Software**, **Finanças** e **Autoajuda**.
- Nenhum empréstimo pré-criado: os registros começam em branco.

**Atenção à identificação dos cartões:** na planilha original os códigos eram `USR001`, `USR002`, `USR003`, mas os adesivos físicos foram impressos como `USR01`, `USR02`, `USR03`. O sistema usa os códigos **USR01, USR02, USR03** para corresponder aos adesivos, preservando os UIDs originais. A planilha original foi incluída sem alterações.

## 1. Executar o projeto no Windows (VS Code)

**Pré-requisitos**: Python 3.10+ e VS Code.

**Opção automática (mais simples):** dê dois cliques em `iniciar_windows.bat`. Na primeira execução ele cria `.venv`, instala as dependências e solicita um usuário e senha para o painel.

**Opção pelo terminal do VS Code (PowerShell):**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python configurar.py
python app.py
```

Se o PowerShell bloquear a ativação, utilize `cmd` com `.venv\Scripts\activate.bat`, ou execute `.venv\Scripts\python.exe` diretamente. O comando `python configurar.py` deve ser executado apenas na configuração inicial, ou novamente quando quiser alterar a senha. As credenciais são definidas por você e **não vêm com uma senha padrão**.

Abra no navegador: **http://127.0.0.1:5000**. Para acessar no celular, conecte o celular à mesma rede Wi-Fi e abra `http://IP_DO_COMPUTADOR:5000`. Descubra o IP IPv4 do PC com `ipconfig`; se necessário, permita o Python/porta 5000 no Firewall do Windows somente na **rede privada**. O Flask `app.run` é adequado a testes na rede local, não à exposição pública.

### Linux e macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python configurar.py
python app.py
```

## 2. O que funciona antes de conectar a ESP32?

- Login de administrador (senha protegida por hash no SQLite).
- Dashboard com livros, disponibilidades, empréstimos, categorias e status de dispositivos.
- Cadastro e edição de livros, categorias, usuários e UID de cartões.
- Empréstimo e devolução manual pelo painel.
- **Simulador RFID:** escolha `Empréstimo` em **Estação RFID**, abra **Simulador**, selecione primeiro `USR01`, depois `LIV001`. O livro muda para *Emprestado*. Para devolver, escolha `Devolução` e leia novamente usuário + livro.
- Histórico de leituras e exportação CSV.
- Banco SQLite persistente em `dados/bibliosocial.db`.

## 3. Cadastrar automaticamente UIDs pelo RC522

Quando o seu colega finalizar a ligação da ESP32:

1. Abra **Livros → Cadastrar livro** ou **Usuários → Cadastrar usuário**.
2. Clique **Ler etiqueta** / **Ler cartão**.
3. A tela aguardará a leitura (75 segundos).
4. Encoste a etiqueta ao RC522; a ESP32 enviará o UID pela API.
5. O campo UID será preenchido automaticamente; complete nome/título/categoria e clique **Salvar**.

Também é possível digitar ou colar o UID usando NFC Tools no celular, mesmo antes do hardware estar montado. Não há necessidade de gravar dados no chip; os UIDs originais são usados para vincular os cadastros. UIDs são identificadores e **não devem ser tratados como autenticação forte** em uso real.

## 4. Estrutura dos arquivos

```text
BiblioSocial_Completo/
├── app.py                       # Executa Flask
├── configurar.py                # Cria administrador e chave da ESP32
├── importar_dados.py            # Atualiza cadastros com CSV
├── requirements.txt
├── iniciar_windows.bat
├── iniciar_linux.sh
├── bibliosocial/
│   ├── __init__.py              # Rotas web e API
│   ├── banco.py                 # Conexões SQLite
│   ├── servicos.py              # Regras de negócio e RFID
│   ├── esquema.sql              # Tabelas, índices, gatilhos
│   ├── templates/               # HTML / Jinja2
│   └── static/css/ e static/js/ # Frontend
├── dados/bibliosocial.db        # Banco preenchido com 5 livros + 3 usuários
├── documentos/
│   ├── cadastroCompleto.xlsx    # Original enviado
│   ├── livros.csv               # Importação facilitada
│   └── usuarios.csv
└── testes/test_aplicacao.py
```

## 5. Importar novamente livros e usuários

Se editar os CSVs de `documentos/`, execute:

```bash
python importar_dados.py
```

Para editar o Excel original, exporte as abas como CSV com separador **ponto e vírgula** e ajuste os nomes das colunas para coincidir com os CSVs incluídos. A importação atualiza registros pelo código e valida UIDs para evitar duplicidade. **Faça backup do banco antes de importar novamente**.

## 6. Endpoints da API ESP32

O dispositivo deve informar a chave gerada na configuração no cabeçalho `X-Chave-Dispositivo`.

- `POST /api/dispositivo/leitura`: `{"uid":"04...","evento_id":"evento-unico","dispositivo":"esp32-principal"}`
- `POST /api/dispositivo/telemetria`: `{"dispositivo":"esp32-principal","distancia_cm":12.5}`
- `GET /api/dispositivo/estado`: modo da estação

O servidor retorna `resultado` (`ok`, `aguardando`, `consulta`, `capturado`, `erro`) e uma `mensagem` para aparecer no monitor serial e acionar os LEDs.

Para evitar eventos duplicados, o backend armazena `evento_id` e devolve a mesma resposta ao receber o mesmo identificador novamente. A gravação utiliza transações SQLite para proteger a consistência de empréstimos simultâneos.

## 7. Testes e limites

Após instalar as dependências no seu computador, rode:

```bash
python -m unittest discover -s testes -v
```

O projeto foi revisado e o banco inicial validado com `PRAGMA integrity_check`. O código do dispositivo deve ser mantido e validado separadamente. O projeto é voltado a demonstração local em ambiente de faculdade: o Flask de desenvolvimento não é para produção e HTTP na rede local não possui criptografia. Para publicar na internet, use HTTPS, autenticação mais robusta, banco/backup e servidor WSGI.

### Dúvidas frequentes

**Posso usar só o celular?** Sim, o frontend é responsivo e abre no navegador do celular conectado à mesma rede do Flask.

**Preciso manter o computador ligado?** Sim, enquanto o backend for local. Depois pode hospedar o Flask numa infraestrutura apropriada e alterar a URL da ESP32.

**O sensor HC-SR04 sabe qual livro foi retirado?** Não: ele informa apenas uma distância. A identificação de cada exemplar depende da etiqueta lida no RC522.

**E se o cartão não for reconhecido no celular?** O RC522 da ESP32 poderá testar cartões NFC-A compatíveis mesmo quando o celular não conseguir. Um cartão RFID de 125 kHz não funcionará com esse leitor.
