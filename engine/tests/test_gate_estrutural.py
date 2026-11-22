"""Story 2.16 (achado M11): divergência estrutural do relatório bloqueia o gate e sai no contrato."""
import pymupdf
from docx import Document

from docredux_engine import api

ESTRUTURA_OK = {
    "h1_capitulos": {"pdf": 18, "modelo": 18}, "h1_total_modelo": 22,
    "legendas": {"pdf": 139, "modelo": 139}, "fontes": {"pdf": 139, "modelo": 139},
    "notas": {"pdf": 67, "modelo": 67}, "referencias_modelo": 385,
    "figuras_sem_legenda": 0, "legendas_sem_visual": 0,
}


def _processar(tmp_path, perfil, monkeypatch, verificacao):
    """processar() com o pipeline controlado: isola a decisão do gate."""
    pdf = tmp_path / "entrada.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), "Texto do original.")
        d.save(pdf)
    docx = tmp_path / "saida.docx"
    Document().save(docx)
    meta = {"paginas": 1, "origem": pdf.name, "stats": {}, "removidas": {}}

    def rodar(*args, **kwargs):
        return {"meta": meta, "modelo": {"meta": meta, "elementos": []}, "verificacao": verificacao, "render": {},
                "docx": docx, "pdf": tmp_path / "saida.pdf", "paginas_pdf": 1, "tempos_s": {}}

    monkeypatch.setattr(api.pipeline, "rodar", rodar)
    (tmp_path / "out").mkdir()
    return api.processar(pdf, api.Opcoes(), perfil, tmp_path / "out").relatorio


def _v(estrutura, ok):
    return {"cobertura_texto_pct": 100.0, "palavras_original": 10, "estrutura": estrutura, "estrutura_ok": ok}


def test_estrutura_divergente_bloqueia_e_descreve_cada_item(tmp_path, perfil_por_nome, monkeypatch):
    est = {**ESTRUTURA_OK, "legendas": {"pdf": 139, "modelo": 137}, "legendas_sem_visual": 1}
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, _v(est, False))
    assert rel.gate == "bloqueado"
    assert rel.motivo_gate == ("estrutura divergente do original: legendas: 139 no original, 137 na saída; "
                               "1 legenda(s) sem figura")
    assert rel.verificacao_estrutural == {
        "ok": False, "contagens": est,
        "divergencias": ["legendas: 139 no original, 137 na saída", "1 legenda(s) sem figura"]}
    assert not any("estrutural" in a for a in rel.avisos)  # antes: só um aviso, com gate ok


def test_cada_tipo_de_divergencia_entra_no_motivo(tmp_path, perfil_por_nome, monkeypatch):
    est = {**ESTRUTURA_OK, "h1_capitulos": {"pdf": 18, "modelo": 17}, "fontes": {"pdf": 139, "modelo": 140},
           "notas": {"pdf": 67, "modelo": 66}, "figuras_sem_legenda": 2}
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, _v(est, False))
    assert rel.gate == "bloqueado"
    assert rel.verificacao_estrutural["divergencias"] == [
        "capítulos: 18 no original, 17 na saída", "Fontes: 139 no original, 140 na saída",
        "notas: 67 no original, 66 na saída", "2 figura(s) sem legenda"]


def test_gate_nao_libera_estrutura_divergente(tmp_path, perfil_por_nome, monkeypatch):
    """Caso da auditoria (docs/qa/motor_auditoria_repro.py): cobertura 100% e estrutura_ok False, sem detalhe."""
    v = {"cobertura_texto_pct": 100, "palavras_original": 2, "estrutura_ok": False}
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, v)
    assert rel.gate == "bloqueado"
    assert rel.motivo_gate == "estrutura divergente do original: capítulos, legendas, Fontes ou notas"


def test_estrutura_conferida_libera_e_sai_no_contrato(tmp_path, perfil_por_nome, monkeypatch):
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, _v(ESTRUTURA_OK, True))
    assert rel.gate == "ok" and rel.motivo_gate is None
    assert rel.verificacao_estrutural == {"ok": True, "divergencias": [], "contagens": ESTRUTURA_OK}
    assert rel.to_dict()["verificacao_estrutural"]["ok"] is True


def test_estrutura_soma_ao_motivo_da_cobertura(tmp_path, perfil_por_nome, monkeypatch):
    v = {**_v({**ESTRUTURA_OK, "notas": {"pdf": 67, "modelo": 60}}, False), "cobertura_texto_pct": 98.0}
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, v)
    assert rel.motivo_gate.startswith("cobertura 98,00% <")
    assert rel.motivo_gate.endswith("estrutura divergente do original: notas: 67 no original, 60 na saída")


def test_ficha_sem_checagem_estrutural_fica_none(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10}
    rel = _processar(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "ok" and rel.verificacao_estrutural is None
