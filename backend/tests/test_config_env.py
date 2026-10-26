from gembti.config import load_env_file


def test_env_file_does_not_override_existing(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# 주석\nSTEAM_WEB_API_KEY='abc'\nexport OTHER_X=1\nKEEP_ME=new\n", encoding="utf-8")
    monkeypatch.delenv("STEAM_WEB_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_X", raising=False)
    monkeypatch.setenv("KEEP_ME", "old")
    load_env_file(f)
    import os
    assert os.environ["STEAM_WEB_API_KEY"] == "abc"
    assert os.environ["OTHER_X"] == "1"
    assert os.environ["KEEP_ME"] == "old"
