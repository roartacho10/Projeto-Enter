"""Regression for Streamlit letters whose recommendation field is a string."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from letter_content import flatten_sections, normalize_sections, parse_sections
from conftest import stage_out


def sample():
    return {"highlights": ["Resumo para Albert."], "greeting": "Albert, segue o relatório.",
            "performance": "Resultado do período.", "macro": "Contexto econômico.",
            "recommendations": ["Sugiro avaliar os ajustes."],
            "coverage": "Cobertura de marcação informada.", "closing": "Podemos conversar."}


def test_string_recommendations_preserve_whole_words_and_figures():
    data = sample()
    paragraph = "Sua carteira apresenta caixa de R$ 74.672,62 (18,18% do patrimônio final)."
    data["recommendations"] = paragraph
    data["highlights"] = "Retorno de 4,07%."
    sections = parse_sections(json.dumps(data, ensure_ascii=False))
    assert sections["recommendations"] == [paragraph]
    assert sections["highlights"] == ["Retorno de 4,07%."]
    letter = flatten_sections(sections, "Antonio Bicudo")
    assert paragraph in letter and "R$ 74.672,62" in letter and "18,18%" in letter
    assert "S\n\nu\n\na" not in letter
    assert len(letter.split()) < 50
    assert data["recommendations"] == paragraph  # caller input untouched


def test_flatten_defensively_normalizes_a_string_without_parse():
    data = sample()
    data["recommendations"] = "Primeiro parágrafo.\n\nSegundo parágrafo."
    assert normalize_sections(data)["recommendations"] == ["Primeiro parágrafo.", "Segundo parágrafo."]
    assert "Primeiro parágrafo.\n\nSegundo parágrafo." in flatten_sections(data, "Assessor")


def test_valid_arrays_are_preserved_and_json_fences_are_supported():
    data = sample()
    data["recommendations"] = ['Manter a palavra "índice".', "Discutir os ajustes."]
    assert parse_sections("```json\n" + json.dumps(data) + "\n```") == data
    assert parse_sections(json.dumps(data)) == data


@pytest.mark.parametrize("value", [None, {}, [], 25, ["Texto", 25], [""], [None]])
def test_bad_paragraph_types_raise_structural_error(value):
    data = sample()
    data["recommendations"] = value
    with pytest.raises(ValueError, match="recommendations"):
        parse_sections(json.dumps(data))


@pytest.mark.parametrize("mutation", ["missing", "scalar_list", "extra", "root_list"])
def test_required_sections_are_validated(mutation):
    data = sample()
    if mutation == "missing": del data["coverage"]
    elif mutation == "scalar_list": data["greeting"] = ["Albert"]
    elif mutation == "extra": data["invented_section"] = "Text"
    else: data = [data]
    with pytest.raises(ValueError):
        parse_sections(json.dumps(data))


def test_malformed_json_is_not_silently_repaired():
    with pytest.raises(json.JSONDecodeError):
        parse_sections('{"recommendations" "missing colon"}')


@pytest.mark.parametrize("retry_structure", [False, True])
def test_generation_handles_string_sections_and_retries_bad_structures(ran, tmp_path, retry_structure):
    repo = tmp_path / "repo"
    shutil.copytree(ran, repo)
    figures = stage_out(ran, "figures.json")
    data = sample()
    data["highlights"] = f"Albert, retorno de {figures['retorno_patrimonio']}."
    data["recommendations"] = f"O saldo em caixa é {figures['caixa_valor']}. Sugiro discutir os ajustes."
    data["coverage"] = f"Cobertura de marcação a mercado: {figures['cobertura_marcacao']}."
    bad = {**data, "recommendations": {"paragraph": "wrong type"}}
    replies = (["{broken json", json.dumps(bad)] if retry_structure else []) + [json.dumps(data)]
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "replies.json").write_text(json.dumps(replies), encoding="utf-8")
    (stub / "openai.py").write_text('''from types import SimpleNamespace
from pathlib import Path
import json
class OpenAI:
    def __init__(self):
        self.replies = iter(json.loads(Path(__file__).with_name("replies.json").read_text(encoding="utf-8")))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        content = "Contexto econômico." if kwargs["model"] == "draft" else next(self.replies)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
''', encoding="utf-8")
    env = dict(os.environ, CLIENT_ID="ALBERT", OPENAI_API_KEY="test", MODEL_DRAFT="draft", MODEL_LETTER="letter",
               PYTHONPATH=str(stub) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    result = subprocess.run([sys.executable, str(repo / "src/generate_letter.py")], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    out = repo / "output/ALBERT"
    log = json.loads((out / "generation_log.json").read_text(encoding="utf-8"))
    saved = json.loads((out / "letter_sections.json").read_text(encoding="utf-8"))
    assert log["approved"] and log["attempts"] == (3 if retry_structure else 1)
    assert saved["recommendations"] == [data["recommendations"]]
    text = (out / "letter.txt").read_text(encoding="utf-8")
    assert figures["caixa_valor"] in text and len(text.split()) < 100
