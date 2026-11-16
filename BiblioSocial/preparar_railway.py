
import os
import shutil
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


RAIZ = Path(__file__).resolve().parent
BANCO_ORIGEM = RAIZ / "dados" / "bibliosocial.db"
ESQUEMA = RAIZ / "bibliosocial" / "esquema.sql"


def preparar():
    caminho = os.environ.get("CAMINHO_BANCO", "").strip()

    if not caminho:
        raise RuntimeError(
            "Configure CAMINHO_BANCO no Railway."
        )

    banco_destino = Path(caminho).expanduser().resolve()
    banco_destino.parent.mkdir(parents=True, exist_ok=True)

    # Copia o banco inicial apenas uma vez.
    if not banco_destino.exists():
        if BANCO_ORIGEM.is_file():
            shutil.copy2(BANCO_ORIGEM, banco_destino)
            print("Banco inicial copiado.")
        else:
            banco_destino.touch()
            print("Banco vazio criado.")

    conexao = sqlite3.connect(str(banco_destino), timeout=15)

    try:
        conexao.execute("PRAGMA foreign_keys=ON")
        conexao.execute("PRAGMA journal_mode=WAL")
        conexao.executescript(
            ESQUEMA.read_text(encoding="utf-8")
        )

        total = conexao.execute(
            "SELECT COUNT(*) FROM administradores"
        ).fetchone()[0]

        # Cria o administrador somente se não existir.
        if total == 0:
            usuario = os.environ.get("ADMIN_USUARIO", "").strip()
            senha = os.environ.get("ADMIN_SENHA", "")

            if not usuario or len(senha) < 12:
                raise RuntimeError(
                    "Configure ADMIN_USUARIO e ADMIN_SENHA "
                    "com senha de pelo menos 12 caracteres."
                )

            conexao.execute(
                """
                INSERT INTO administradores (usuario, senha_hash)
                VALUES (?, ?)
                """,
                (usuario, generate_password_hash(senha))
            )
            conexao.commit()
            print("Administrador criado.")
        else:
            print("Administrador já cadastrado.")

        livros = conexao.execute(
            "SELECT COUNT(*) FROM livros"
        ).fetchone()[0]

        usuarios = conexao.execute(
            "SELECT COUNT(*) FROM usuarios"
        ).fetchone()[0]

        print(f"Banco pronto: {livros} livros e {usuarios} usuários.")

    finally:
        conexao.close()


if __name__ == "__main__":
    preparar()
