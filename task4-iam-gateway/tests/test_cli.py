import io

from ollama_iam_gateway import cli


def run(monkeypatch, db_url, *args, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(["--db", db_url, *args])


def test_init_db_creates_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "iam.db"
    assert run(monkeypatch, f"sqlite:///{path}", "init-db") == 0
    assert path.exists()


def test_create_admin_then_list(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'iam.db'}"
    assert run(monkeypatch, url, "create-admin", "--username", "Admin",
               "--password-stdin", stdin="supersecret\n") == 0
    assert "Created admin 'admin'" in capsys.readouterr().out
    assert run(monkeypatch, url, "list-users") == 0
    out = capsys.readouterr().out
    assert "admin" in out and "yes" in out


def test_create_admin_errors_are_reported(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'iam.db'}"
    run(monkeypatch, url, "create-admin", "--username", "admin",
        "--password-stdin", stdin="supersecret\n")
    capsys.readouterr()
    # duplicate
    assert run(monkeypatch, url, "create-admin", "--username", "admin",
               "--password-stdin", stdin="supersecret\n") == 1
    assert "already exists" in capsys.readouterr().err
    # weak password
    assert run(monkeypatch, url, "create-admin", "--username", "admin2",
               "--password-stdin", stdin="123\n") == 1
    assert "at least 8" in capsys.readouterr().err


def test_password_prompt_mismatch(tmp_path, monkeypatch, capsys):
    answers = iter(["password123", "different123"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert cli.main(["--db", f"sqlite:///{tmp_path / 'iam.db'}",
                     "create-admin", "--username", "admin"]) == 1
    assert "do not match" in capsys.readouterr().err
