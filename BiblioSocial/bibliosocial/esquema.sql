PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS administradores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  usuario TEXT NOT NULL UNIQUE,
  senha_hash TEXT NOT NULL,
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS livros (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  codigo TEXT NOT NULL UNIQUE,
  titulo TEXT NOT NULL,
  autor TEXT NOT NULL DEFAULT '',
  categoria TEXT NOT NULL DEFAULT 'Geral',
  isbn TEXT NOT NULL DEFAULT '',
  uid TEXT UNIQUE,
  localizacao TEXT NOT NULL DEFAULT '',
  observacoes TEXT NOT NULL DEFAULT '',
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  codigo TEXT NOT NULL UNIQUE,
  nome TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  uid TEXT UNIQUE,
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS emprestimos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  livro_id INTEGER NOT NULL REFERENCES livros(id) ON DELETE RESTRICT,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
  emprestado_em TEXT NOT NULL DEFAULT (datetime('now')),
  devolvido_em TEXT,
  origem TEXT NOT NULL DEFAULT 'painel' CHECK (origem IN ('painel','rfid'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_emprestimos_livro_ativo
  ON emprestimos(livro_id) WHERE devolvido_em IS NULL;

CREATE TABLE IF NOT EXISTS capturas (
  id TEXT PRIMARY KEY,
  tipo TEXT NOT NULL CHECK (tipo IN ('livro','usuario')),
  estado TEXT NOT NULL DEFAULT 'aguardando' CHECK (estado IN ('aguardando','capturado','cancelado','expirado')),
  uid TEXT,
  criada_em INTEGER NOT NULL,
  expira_em INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS estado_operacao (
  id INTEGER PRIMARY KEY CHECK (id=1),
  modo TEXT NOT NULL DEFAULT 'consulta' CHECK (modo IN ('consulta','emprestimo','devolucao')),
  usuario_pendente INTEGER REFERENCES usuarios(id),
  usuario_expira_em INTEGER,
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO estado_operacao(id, modo) VALUES(1,'consulta');

CREATE TABLE IF NOT EXISTS leituras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evento_id TEXT UNIQUE,
  dispositivo TEXT NOT NULL DEFAULT 'esp32-principal',
  uid TEXT NOT NULL,
  resultado TEXT NOT NULL,
  mensagem TEXT NOT NULL,
  resposta_json TEXT NOT NULL DEFAULT '{}',
  criada_em TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_leituras_data ON leituras(id DESC);

CREATE TABLE IF NOT EXISTS dispositivos (
  identificador TEXT PRIMARY KEY,
  ultima_atividade TEXT NOT NULL DEFAULT (datetime('now')),
  distancia_cm REAL
);

-- Extensões compatíveis com a aplicação Flask BiblioSocial.
-- Impedem que um UID seja atribuído simultaneamente a um livro e a um usuário.
CREATE TRIGGER IF NOT EXISTS verificar_uid_livro_insercao
BEFORE INSERT ON livros
WHEN NEW.uid IS NOT NULL AND EXISTS (SELECT 1 FROM usuarios WHERE uid = NEW.uid)
BEGIN
  SELECT RAISE(ABORT, 'UID ja cadastrado como cartao de usuario');
END;

CREATE TRIGGER IF NOT EXISTS verificar_uid_livro_atualizacao
BEFORE UPDATE OF uid ON livros
WHEN NEW.uid IS NOT NULL AND EXISTS (SELECT 1 FROM usuarios WHERE uid = NEW.uid)
BEGIN
  SELECT RAISE(ABORT, 'UID ja cadastrado como cartao de usuario');
END;

CREATE TRIGGER IF NOT EXISTS verificar_uid_usuario_insercao
BEFORE INSERT ON usuarios
WHEN NEW.uid IS NOT NULL AND EXISTS (SELECT 1 FROM livros WHERE uid = NEW.uid)
BEGIN
  SELECT RAISE(ABORT, 'UID ja cadastrado como etiqueta de livro');
END;

CREATE TRIGGER IF NOT EXISTS verificar_uid_usuario_atualizacao
BEFORE UPDATE OF uid ON usuarios
WHEN NEW.uid IS NOT NULL AND EXISTS (SELECT 1 FROM livros WHERE uid = NEW.uid)
BEGIN
  SELECT RAISE(ABORT, 'UID ja cadastrado como etiqueta de livro');
END;

CREATE TRIGGER IF NOT EXISTS impedir_emprestimo_usuario_inativo
BEFORE INSERT ON emprestimos
WHEN (SELECT ativo FROM usuarios WHERE id = NEW.usuario_id) = 0
BEGIN
  SELECT RAISE(ABORT, 'Usuario inativo nao pode emprestar livros');
END;

CREATE TRIGGER IF NOT EXISTS validar_devolucao
BEFORE UPDATE OF devolvido_em ON emprestimos
WHEN NEW.devolvido_em IS NOT NULL AND NEW.devolvido_em < NEW.emprestado_em
BEGIN
  SELECT RAISE(ABORT, 'Devolucao anterior a data do emprestimo');
END;

CREATE INDEX IF NOT EXISTS idx_emprestimos_usuario ON emprestimos(usuario_id, devolvido_em);
CREATE INDEX IF NOT EXISTS idx_livros_categoria ON livros(categoria);
CREATE INDEX IF NOT EXISTS idx_leituras_uid ON leituras(uid, criada_em);


-- Reservas de livros são distintas dos empréstimos. Apenas uma ativa por exemplar.
CREATE TABLE IF NOT EXISTS reservas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  livro_id INTEGER NOT NULL REFERENCES livros(id) ON DELETE RESTRICT,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
  situacao TEXT NOT NULL DEFAULT 'ativa' CHECK (situacao IN ('ativa','cancelada','retirada')),
  origem TEXT NOT NULL DEFAULT 'rfid',
  reservada_em TEXT NOT NULL DEFAULT (datetime('now')),
  encerrada_em TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reserva_ativa_livro ON reservas(livro_id) WHERE situacao='ativa';
CREATE INDEX IF NOT EXISTS idx_reservas_usuario ON reservas(usuario_id,situacao);

CREATE TABLE IF NOT EXISTS sessoes_reserva (
  id TEXT PRIMARY KEY,
  livro_id INTEGER NOT NULL REFERENCES livros(id) ON DELETE CASCADE,
  estado TEXT NOT NULL CHECK (estado IN ('aguardando_livro','aguardando_usuario','concluida','cancelada','expirada')),
  criada_em INTEGER NOT NULL,
  expira_em INTEGER NOT NULL,
  reserva_id INTEGER REFERENCES reservas(id),
  mensagem TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessao_reserva_pendente ON sessoes_reserva(1)
 WHERE estado IN ('aguardando_livro','aguardando_usuario');

DROP VIEW IF EXISTS visao_livros;
CREATE VIEW visao_livros AS
SELECT l.id, l.codigo, l.titulo, l.autor, l.categoria, l.isbn,
       l.uid, l.localizacao,
       CASE WHEN EXISTS (
           SELECT 1 FROM emprestimos e
           WHERE e.livro_id = l.id AND e.devolvido_em IS NULL
       ) THEN 'Emprestado'
       WHEN EXISTS (
           SELECT 1 FROM reservas r
           WHERE r.livro_id = l.id AND r.situacao = 'ativa'
       ) THEN 'Reservado' ELSE 'Disponível' END AS situacao,
       l.criado_em
FROM livros l;

CREATE VIEW IF NOT EXISTS visao_emprestimos_ativos AS
SELECT e.id, l.codigo AS codigo_livro, l.titulo AS titulo_livro,
       u.codigo AS codigo_usuario, u.nome AS nome_usuario,
       e.emprestado_em, e.origem
FROM emprestimos e
INNER JOIN livros l ON l.id = e.livro_id
INNER JOIN usuarios u ON u.id = e.usuario_id
WHERE e.devolvido_em IS NULL;

PRAGMA user_version = 2;
